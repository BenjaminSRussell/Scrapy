"""#450: server-side fetches refuse private, loopback, link-local and metadata destinations."""

import socket
from pathlib import Path

import pytest
from aiohttp import web
from prometheus_client import REGISTRY

from src.stage2.stage2_worker import Stage2Worker
from src.utils import url_safety
from src.utils.url_safety import UnsafeURLError, check_url, is_blocked_ip, safe_get


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch):
    monkeypatch.delenv("FETCH_ALLOWED_CIDRS", raising=False)


def _blocked_total():
    return REGISTRY.get_sample_value("stage2_unsafe_urls_blocked_total") or 0.0


# ------------------------------------------------------------------ addresses
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1",  # loopback + RFC 1918
        "169.254.169.254",  # cloud metadata (link-local)
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255",
        "::1", "fe80::1", "fc00::1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
    ],
)
def test_non_public_addresses_blocked(ip):
    assert is_blocked_ip(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "137.99.1.1", "2001:4860:4860::8888"])
def test_public_addresses_allowed(ip):
    assert not is_blocked_ip(ip)


def test_allowlist_opts_ranges_back_in(monkeypatch):
    monkeypatch.setenv("FETCH_ALLOWED_CIDRS", "10.20.0.0/16, bogus")
    assert not is_blocked_ip("10.20.3.4")
    assert is_blocked_ip("10.21.0.1")
    assert is_blocked_ip("169.254.169.254")


# ------------------------------------------------------------------ URLs
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/",
        "http://2130706433/",  # 127.0.0.1 as an integer
        "http://10.0.0.5:6379/",
        "file:///etc/passwd",
        "gopher://uconn.edu/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http:///nohost",
    ],
)
def test_unsafe_urls_rejected_without_dns(url):
    with pytest.raises(UnsafeURLError):
        check_url(url, resolve=False)


def _fake_dns(monkeypatch, address):
    def getaddrinfo(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", getaddrinfo)


def test_hostname_resolving_to_private_ip_rejected(monkeypatch):
    _fake_dns(monkeypatch, "10.0.0.7")
    with pytest.raises(UnsafeURLError, match="non-public"):
        check_url("https://evil.example.org/")


def test_hostname_resolving_to_public_ip_allowed(monkeypatch):
    _fake_dns(monkeypatch, "137.99.1.1")
    check_url("https://uconn.edu/")


def test_unresolvable_host_is_left_to_the_fetch(monkeypatch):
    def fail(*a, **k):
        raise socket.gaierror("nx")

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fail)
    check_url("https://no-such-host.invalid/")


# ------------------------------------------------------------------ requests (ASR)
class _Resp:
    def __init__(self, status=200, location=None):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None
        self.closed = False

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_safe_get_allows_public_and_follows_safe_redirects(monkeypatch):
    _fake_dns(monkeypatch, "137.99.1.1")
    s = _Session([_Resp(302, "/media/b.wav"), _Resp(200)])
    resp = safe_get("https://uconn.edu/media/a.wav", session=s, timeout=5)
    assert resp.status_code == 200
    assert [c[0] for c in s.calls] == ["https://uconn.edu/media/a.wav", "https://uconn.edu/media/b.wav"]
    assert all(c[1]["allow_redirects"] is False for c in s.calls)


def test_safe_get_refuses_redirect_to_metadata(monkeypatch):
    _fake_dns(monkeypatch, "137.99.1.1")
    s = _Session([_Resp(302, "http://169.254.169.254/latest/meta-data/iam/")])
    with pytest.raises(UnsafeURLError):
        safe_get("https://uconn.edu/a.wav", session=s)
    assert len(s.calls) == 1  # the metadata URL was never requested


def test_safe_get_refuses_unsafe_start_without_request():
    s = _Session([])
    with pytest.raises(UnsafeURLError):
        safe_get("http://127.0.0.1:6379/", session=s)
    assert s.calls == []


def test_asr_download_refuses_internal_url_and_leaves_no_temp_file(tmp_path):
    from src.common.async_asr_processor import AsyncASRProcessor

    proc = AsyncASRProcessor(max_workers=1, temp_dir=str(tmp_path))
    try:
        with pytest.raises(UnsafeURLError):
            proc._download_media("http://169.254.169.254/latest/meta-data/x.wav")
        assert list(Path(tmp_path).iterdir()) == []
    finally:
        shutdown = getattr(proc, "shutdown", None)
        if callable(shutdown):
            shutdown()


# ------------------------------------------------------------------ Stage 2
@pytest.fixture
async def server():
    hits = []

    async def page(request):
        hits.append(request.path)
        if request.path == "/to-metadata":
            raise web.HTTPFound("http://169.254.169.254/latest/meta-data/")
        return web.Response(text="<html><title>t</title><body>" + "<p>w " * 50 + "</body></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/{tail:.*}", page)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield port, hits
    await runner.cleanup()


@pytest.fixture
def worker():
    w = Stage2Worker(max_concurrent=2)
    w.http_attempts = 3
    w._retry_delay = lambda attempt, retry_after=None: 0.0
    return w


async def test_stage2_refuses_loopback_target_without_connecting(server, worker):
    port, hits = server
    before = _blocked_total()
    rec = await worker._analyze_url({"url": f"http://127.0.0.1:{port}/admin", "url_hash": "h"})
    assert rec["has_error"] and rec["error_message"].startswith("blocked_unsafe_url")
    assert hits == []
    assert _blocked_total() == before + 1


async def test_stage2_refuses_redirect_to_metadata(server, worker, monkeypatch):
    port, hits = server
    monkeypatch.setenv("FETCH_ALLOWED_CIDRS", "127.0.0.1/32")  # the test server itself is "public"
    rec = await worker._analyze_url({"url": f"http://127.0.0.1:{port}/to-metadata", "url_hash": "h"})
    assert rec["error_message"].startswith("blocked_unsafe_url")
    assert hits == ["/to-metadata"]  # one request, no retries, metadata never contacted


async def test_stage2_refuses_hostname_resolving_to_loopback(server, worker):
    port, hits = server
    before = _blocked_total()
    rec = await worker._analyze_url({"url": f"http://localhost:{port}/x", "url_hash": "h"})
    assert rec["error_message"].startswith("blocked_unsafe_url")
    assert hits == []  # refused at DNS time by the connector's resolver, not retried
    assert _blocked_total() == before + 1


async def test_stage2_allowed_destination_still_fetches(server, worker, monkeypatch):
    port, hits = server
    monkeypatch.setenv("FETCH_ALLOWED_CIDRS", "127.0.0.1/32")
    rec = await worker._analyze_url({"url": f"http://127.0.0.1:{port}/ok", "url_hash": "h"})
    assert not rec.get("has_error")
    assert hits == ["/ok"]
