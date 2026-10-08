"""#450: SSRF guard on DNS answers (Stage 2 connector) and the ASR media downloader."""

from __future__ import annotations

import asyncio
import os
import socket
from types import SimpleNamespace

import aiohttp
import pytest
from prometheus_client import REGISTRY

from src.utils import ssrf
from src.utils.ssrf import (
    SSRFBlocked,
    SSRFResolveBlocked,
    guarded_get,
    resolved_block_reason,
    safe_resolver,
    ssrf_error_from,
)


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch):
    monkeypatch.delenv("SSRF_ALLOWED_HOSTS", raising=False)


def blocked_count(stage: str, reason: str) -> float:
    return REGISTRY.get_sample_value("scrapy_ssrf_blocked_total", {"stage": stage, "reason": reason}) or 0.0


# --- resolved_block_reason ---------------------------------------------------


@pytest.mark.parametrize(
    "addresses, expected",
    [
        (["93.184.216.34"], None),
        (["93.184.216.34", "10.0.0.7"], "dns_private"),
        (["169.254.169.254"], "dns_link_local"),
        (["127.0.0.1"], "dns_loopback"),
        (["::ffff:127.0.0.1"], "dns_loopback"),
        (["fd00::1"], "dns_private"),
        (["100.64.1.1"], "dns_non_global"),
        (["2606:4700::6810:84e5"], None),
    ],
)
def test_resolved_block_reason(addresses, expected):
    assert resolved_block_reason("www.example.edu", addresses) == expected


def test_resolved_block_reason_respects_allowlist():
    assert resolved_block_reason("intranet.example.edu", ["10.20.1.5"], allowed_hosts="10.20.0.0/16") is None
    assert resolved_block_reason("intranet.example.edu", ["10.20.1.5"], allowed_hosts="intranet.example.edu") is None
    assert resolved_block_reason("intranet.example.edu", ["10.30.1.5"], allowed_hosts="10.20.0.0/16") == "dns_private"


# --- aiohttp resolver ----------------------------------------------------------


class _FakeInnerResolver:
    """Maps hostnames to fixed addresses (no real DNS)."""

    table: dict[str, str] = {}

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return [
            {"hostname": host, "host": self.table[host], "port": port, "family": family, "proto": 0, "flags": 0}
        ]

    async def close(self):
        pass


@pytest.fixture
def fake_dns(monkeypatch):
    monkeypatch.setattr(aiohttp, "DefaultResolver", _FakeInnerResolver)
    _FakeInnerResolver.table = {}
    return _FakeInnerResolver.table


def test_safe_resolver_refuses_internal_answers(fake_dns):
    fake_dns.update({"rebind.example.com": "169.254.169.254", "ok.example.com": "93.184.216.34"})
    resolver = safe_resolver("stage2")
    before = blocked_count("stage2", "dns_link_local")

    async def go():
        ok = await resolver.resolve("ok.example.com", 80)
        with pytest.raises(SSRFResolveBlocked) as info:
            await resolver.resolve("rebind.example.com", 80)
        await resolver.close()
        return ok, info.value

    ok, err = asyncio.run(go())
    assert ok[0]["host"] == "93.184.216.34"
    assert err.reason == "dns_link_local" and isinstance(err, OSError)
    assert blocked_count("stage2", "dns_link_local") == before + 1


def test_aiohttp_connector_with_safe_resolver_never_connects_to_internal(fake_dns):
    """End to end: a public-looking hostname that resolves to loopback is refused before connecting."""
    import http.server
    import threading

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    fake_dns["innocent.example.com"] = "127.0.0.1"

    async def go():
        connector = aiohttp.TCPConnector(resolver=safe_resolver("stage2"))
        async with aiohttp.ClientSession(connector=connector) as session:
            with pytest.raises(aiohttp.ClientConnectionError) as info:
                await session.get(f"http://innocent.example.com:{port}/secret")
        return info.value

    try:
        err = asyncio.run(go())
    finally:
        server.shutdown()
        server.server_close()
    assert hits == []  # the internal service was never reached
    blocked = ssrf_error_from(err)
    assert blocked is not None and blocked.reason == "dns_loopback"


def test_ssrf_error_from_walks_causes():
    inner = SSRFResolveBlocked("h", "dns_private")
    outer = RuntimeError("wrapped")
    outer.__cause__ = inner
    assert ssrf_error_from(outer) is inner
    assert ssrf_error_from(SimpleNamespace(os_error=inner, __cause__=None, __context__=None)) is inner  # type: ignore[arg-type]
    assert ssrf_error_from(ValueError("x")) is None


def test_stage2_session_uses_the_safe_resolver():
    from src.stage2.stage2_worker import Stage2Worker

    worker = Stage2Worker.__new__(Stage2Worker)
    worker.max_concurrent = 4
    worker._per_host_limit = lambda: 2  # type: ignore[method-assign]

    async def go():
        session = worker._new_session()
        try:
            return type(session.connector._resolver).__name__
        finally:
            await session.close()

    assert asyncio.run(go()) == "SSRFSafeResolver"


# --- guarded_get (ASR / requests) ---------------------------------------------


class _Resp:
    def __init__(self, status, location=None, url=""):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.url = url
        self.closed = False

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def get(self, url, allow_redirects=True, **kwargs):
        assert allow_redirects is False  # redirects are followed by hand
        self.calls.append(url)
        return self.responses[url]


@pytest.fixture
def public_dns(monkeypatch):
    table = {"media.example.edu": "93.184.216.34", "cdn.example.net": "93.184.216.35", "evil.example.com": "10.9.8.7"}

    def fake_getaddrinfo(host, *args, **kwargs):
        if host not in table:
            raise socket.gaierror("unknown")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], 0))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    return table


def test_guarded_get_follows_public_redirects(public_dns):
    final = _Resp(200, url="https://cdn.example.net/a.mp3")
    first = _Resp(302, location="https://cdn.example.net/a.mp3", url="https://media.example.edu/a.mp3")
    session = _Session({"https://media.example.edu/a.mp3": first, "https://cdn.example.net/a.mp3": final})
    assert guarded_get("https://media.example.edu/a.mp3", session=session, timeout=5) is final
    assert first.closed and session.calls == ["https://media.example.edu/a.mp3", "https://cdn.example.net/a.mp3"]


def test_guarded_get_blocks_redirect_to_metadata(public_dns):
    hop = _Resp(301, location="http://169.254.169.254/latest/meta-data/", url="https://media.example.edu/a.mp3")
    session = _Session({"https://media.example.edu/a.mp3": hop})
    before = blocked_count("asr", "ip_link_local")
    with pytest.raises(SSRFBlocked) as info:
        guarded_get("https://media.example.edu/a.mp3", session=session)
    assert info.value.reason == "ip_link_local"
    assert session.calls == ["https://media.example.edu/a.mp3"]  # metadata endpoint never requested
    assert blocked_count("asr", "ip_link_local") == before + 1


def test_guarded_get_blocks_hostname_resolving_internal(public_dns):
    session = _Session({})
    with pytest.raises(SSRFBlocked) as info:
        guarded_get("https://evil.example.com/a.wav", session=session)
    assert info.value.reason == "dns_private" and session.calls == []


def test_guarded_get_relative_redirect_and_loop_cap(public_dns):
    loop = _Resp(302, location="/again.mp3", url="https://media.example.edu/again.mp3")
    session = _Session({"https://media.example.edu/a.mp3": loop, "https://media.example.edu/again.mp3": loop})
    with pytest.raises(SSRFBlocked) as info:
        guarded_get("https://media.example.edu/a.mp3", session=session, max_redirects=3)
    assert info.value.reason == "too_many_redirects" and len(session.calls) == 4


def test_asr_download_refuses_internal_media_and_cleans_temp(tmp_path, monkeypatch):
    pytest.importorskip("twisted")
    from src.common import async_asr_processor as asr

    proc = asr.AsyncASRProcessor.__new__(asr.AsyncASRProcessor)
    proc.temp_dir = str(tmp_path)
    proc.max_download_bytes = None
    with pytest.raises(SSRFBlocked):
        proc._download_media("http://169.254.169.254/latest/audio.mp3")
    with pytest.raises(SSRFBlocked):
        proc._download_media("http://redis:6379/x.wav")
    assert os.listdir(tmp_path) == []  # temp file removed on refusal
