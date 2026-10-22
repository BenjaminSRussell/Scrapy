"""#1629: Stage 2 sends the configured User-Agent, obeys robots.txt and Crawl-delay, and uses the egress proxy pool."""

import asyncio
import time

import pytest
from aiohttp import web
from prometheus_client import REGISTRY

import src.stage2.stage2_worker as sw
from src import settings as project
from src.stage2.stage2_worker import Stage2Worker
from src.utils.http_identity import EgressProxies, HttpPolicy, NoHealthyProxy
from src.utils.robots_gate import RobotsGate

ARTICLE = "<html><head><title>T</title></head><body><p>" + "word " * 200 + "</p></body></html>"
PROXY = "http://proxy-a.example:3128"


class Server:
    """Local origin that records each request's path, User-Agent and arrival time."""

    def __init__(self, robots: str | None):
        self.robots = robots
        self.hits: list[tuple[str, str, float]] = []

    async def handler(self, request):
        self.hits.append((request.path, request.headers.get("User-Agent", ""), time.monotonic()))
        if request.path == "/robots.txt":
            if self.robots is None:
                return web.Response(status=404, text="nope")
            return web.Response(status=200, text=self.robots, content_type="text/plain")
        return web.Response(status=200, text=ARTICLE, content_type="text/html")

    def paths(self):
        return [path for path, _, _ in self.hits]


async def _serve(robots):
    server = Server(robots)
    app = web.Application()
    app.router.add_get("/{tail:.*}", server.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]
    server.base = f"http://127.0.0.1:{port}"
    return server, runner


def _policy(**overrides):
    values = dict(
        user_agent="UConn-Test-Crawler/3.1",
        robotstxt_obey=True,
        robots_max_crawl_delay=60.0,
        proxy_list=(),
        proxy_enabled=False,
        proxy_quarantine_seconds=300.0,
    )
    values.update(overrides)
    return HttpPolicy(**values)


@pytest.fixture
def worker(monkeypatch):
    # The local origin is on 127.0.0.1, which the SSRF guard blocks unless allow-listed (#682).
    monkeypatch.setenv("SSRF_ALLOWED_HOSTS", "127.0.0.1,localhost")
    w = Stage2Worker(max_concurrent=4)
    w._policy = _policy()
    return w


@pytest.fixture
async def origin():
    holder = {}

    async def start(robots):
        server, runner = await _serve(robots)
        holder["runner"] = runner
        return server

    yield start
    if "runner" in holder:
        await holder["runner"].cleanup()


# --- policy resolution -------------------------------------------------------


def test_policy_reads_scrapy_config_and_env_overrides():
    cfg = {"user_agent": "Bot/9.0", "robotstxt_obey": False, "robots_max_crawl_delay": 5}
    policy = HttpPolicy.from_settings(cfg, env={})
    assert policy.user_agent == "Bot/9.0"
    assert policy.robotstxt_obey is False
    assert policy.robots_max_crawl_delay == 5.0
    assert policy.headers() == {"User-Agent": "Bot/9.0"}

    overridden = HttpPolicy.from_settings(cfg, env={"ROBOTSTXT_OBEY": "1", "ROBOTS_MAX_CRAWL_DELAY": "9"})
    assert overridden.robotstxt_obey is True
    assert overridden.robots_max_crawl_delay == 9.0


def test_policy_matches_stage1_settings():
    policy = HttpPolicy.from_settings()
    assert policy.user_agent == project.USER_AGENT
    assert policy.robotstxt_obey == project.ROBOTSTXT_OBEY
    assert policy.robots_max_crawl_delay == project.ROBOTS_MAX_CRAWL_DELAY
    assert policy.proxy_list == tuple(project.PROXY_LIST)


# --- User-Agent --------------------------------------------------------------


async def test_stage2_session_carries_configured_user_agent(worker):
    session = worker._new_session()
    try:
        assert session.headers["User-Agent"] == "UConn-Test-Crawler/3.1"
    finally:
        await session.close()


async def test_stage2_requests_send_configured_user_agent(worker, origin):
    server = await origin(None)
    worker._policy = _policy(robotstxt_obey=False)
    worker._session = worker._new_session()
    try:
        rec = await worker._analyze_url({"url": f"{server.base}/ok", "url_hash": "h"})
    finally:
        await worker._session.close()
        worker._session = None
    assert rec["has_error"] is False
    assert [ua for path, ua, _ in server.hits if path == "/ok"] == ["UConn-Test-Crawler/3.1"]


# --- robots.txt --------------------------------------------------------------


async def test_disallowed_url_is_never_requested_and_ends_robots_disallowed(worker, origin):
    server = await origin("User-agent: *\nDisallow: /private\n")
    worker._session = worker._new_session()
    before = REGISTRY.get_sample_value("stage2_robots_disallowed_total") or 0.0
    try:
        allowed = await worker._analyze_url({"url": f"{server.base}/ok", "url_hash": "a"})
        refused = await worker._analyze_url({"url": f"{server.base}/private", "url_hash": "b"})
    finally:
        await worker._session.close()
        worker._session = None

    assert allowed["has_error"] is False
    assert refused["has_error"] is True
    assert refused["error_message"] == "robots_disallowed"
    assert "/private" not in server.paths()
    assert sw._is_terminal_error(refused)  # a refusal is not retried
    assert REGISTRY.get_sample_value("stage2_robots_disallowed_total") == before + 1


async def test_robots_disabled_lets_disallowed_url_through(worker, origin):
    server = await origin("User-agent: *\nDisallow: /private\n")
    worker._policy = _policy(robotstxt_obey=False)
    worker._session = worker._new_session()
    try:
        rec = await worker._analyze_url({"url": f"{server.base}/private", "url_hash": "p"})
    finally:
        await worker._session.close()
        worker._session = None
    assert rec["has_error"] is False
    assert "/robots.txt" not in server.paths()


async def test_crawl_delay_spaces_successive_requests_to_one_host(worker, origin):
    server = await origin("User-agent: *\nCrawl-delay: 1\n")
    worker._session = worker._new_session()
    try:
        await asyncio.gather(
            worker._analyze_url({"url": f"{server.base}/a", "url_hash": "a"}),
            worker._analyze_url({"url": f"{server.base}/b", "url_hash": "b"}),
        )
    finally:
        await worker._session.close()
        worker._session = None
    times = {path: t for path, _, t in server.hits}
    assert abs(times["/b"] - times["/a"]) >= 0.95  # a 1s Crawl-delay, with timer slack


# --- egress proxies ----------------------------------------------------------


class _RecordingSession:
    """Answers every GET with ``status`` and remembers the proxy each one was sent through."""

    def __init__(self, status=200):
        self.status = status
        self.proxies: list[str | None] = []

    async def get(self, url, **kwargs):
        self.proxies.append(kwargs.get("proxy"))
        return _FakeResponse(self.status)


class _FakeResponse:
    def __init__(self, status):
        self.status = status
        self.headers = {}
        self.content_length = None
        self.charset = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def release(self):
        pass


async def test_stage2_requests_use_the_configured_proxy(worker):
    worker._policy = _policy(proxy_list=(PROXY,), proxy_enabled=True)
    session = _RecordingSession(status=200)
    await worker._get_guarded(session, "http://example.edu/", worker._egress().pick())
    assert session.proxies == [PROXY]


async def test_stage2_defers_when_every_proxy_is_quarantined(worker):
    worker._policy = _policy(proxy_list=(PROXY,), proxy_enabled=True)
    worker._egress().quarantine(PROXY)
    session = _RecordingSession()
    rec = await worker._analyze_url({"url": "http://example.edu/x", "url_hash": "q"})
    assert rec == {"url": "http://example.edu/x", "url_hash": "q", "_deferred": True}
    with pytest.raises(NoHealthyProxy):
        await worker._fetch_once(session, "http://example.edu/x", "q", False, "example.edu")
    assert session.proxies == []  # nothing went out, least of all directly


async def test_proxy_407_quarantines_the_proxy_used(worker):
    worker._policy = _policy(proxy_list=(PROXY,), proxy_enabled=True)
    rec = await worker._fetch_once(_RecordingSession(status=407), "http://example.edu/x", "h", False, "example.edu")
    assert rec["has_error"] is True
    with pytest.raises(NoHealthyProxy):
        worker._egress().pick()


def test_egress_disabled_without_proxy_list():
    pool = EgressProxies(_policy(proxy_list=(), proxy_enabled=True))
    assert pool.enabled is False
    assert pool.pick() is None


# --- RobotsGate (no network) -------------------------------------------------


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def _gate(body, *, cap=60.0, clock=None):
    calls = []

    async def fetch(robots_url):
        calls.append(robots_url)
        return body

    gate = RobotsGate("UConn-Test-Crawler/3.1", fetch, max_crawl_delay=cap, clock=clock or _Clock())
    return gate, calls


async def test_gate_fetches_robots_once_per_origin_and_applies_disallow():
    gate, calls = await _gate(b"User-agent: *\nDisallow: /private\n")
    assert await gate.check("https://site.edu/ok") == (True, 0.0)
    assert await gate.check("https://site.edu/private/x") == (False, 0.0)
    assert calls == ["https://site.edu/robots.txt"]


async def test_gate_missing_robots_allows_everything():
    gate, _ = await _gate(None)
    assert await gate.check("https://site.edu/anything") == (True, 0.0)


async def test_gate_caps_crawl_delay():
    gate, _ = await _gate(b"User-agent: *\nCrawl-delay: 30\n", cap=5.0)
    assert await gate.check("https://site.edu/a") == (True, 5.0)


async def test_gate_reserve_spaces_starts_by_the_delay():
    clock = _Clock()
    gate, _ = await _gate(None, clock=clock)
    assert gate.reserve("site.edu", 2.0) == 0.0
    assert gate.reserve("site.edu", 2.0) == 2.0
    clock.now += 1.0
    assert gate.reserve("site.edu", 2.0) == 3.0  # third start is at t=1004, now is t=1001
    assert gate.reserve("other.edu", 2.0) == 0.0  # other hosts are not held back
