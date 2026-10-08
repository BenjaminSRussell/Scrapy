"""#456: global kill switch + budgets, honored by Stage 1 and Stage 2 within the check interval."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import fakeredis
import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Request

from src.utils import crawl_guard as cg


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _no_env_switch(monkeypatch):
    monkeypatch.delenv("CRAWL_KILL_SWITCH", raising=False)


@pytest.fixture
def r():
    return fakeredis.FakeRedis()


def test_engage_is_seen_by_other_workers_within_check_interval(r):
    clock = _Clock()
    worker = cg.KillSwitch(client=r, check_secs=5, clock=clock)
    operator = cg.KillSwitch(client=r)
    assert worker.engaged() is False
    operator.engage(reason="abuse report", actor="oncall")
    clock.t = 4.9
    assert worker.engaged() is False  # cached; inside SLA window
    clock.t = 5.0
    assert worker.engaged() is True
    st = worker.state()
    assert (st.reason, st.actor) == ("abuse report", "oncall")


def test_release_and_audit_log(r):
    sw = cg.KillSwitch(client=r)
    sw.engage(reason="flood", actor="alice")
    sw.release(actor="bob", reason="fixed")
    assert sw.engaged() is False
    audit = sw.audit()
    assert [a["action"] for a in audit] == ["release", "engage"]
    assert audit[1]["actor"] == "alice" and audit[0]["reason"] == "fixed" and audit[0]["at"]


def test_engage_requires_reason_and_actor(r):
    with pytest.raises(ValueError):
        cg.KillSwitch(client=r).engage(reason="", actor="x")
    with pytest.raises(ValueError):
        cg.KillSwitch(client=r).release(actor=" ")


def test_env_break_glass_works_without_redis(monkeypatch):
    class _Down:
        def hgetall(self, key):
            raise ConnectionError("redis down")

    monkeypatch.setenv("CRAWL_KILL_SWITCH", "1")
    st = cg.KillSwitch(client=_Down()).state()
    assert st.engaged and st.source == "env"


def test_redis_blip_keeps_last_known_state(r):
    clock = _Clock()
    sw = cg.KillSwitch(client=r, check_secs=1, clock=clock)
    cg.KillSwitch(client=r).engage(reason="x", actor="y")
    assert sw.engaged()

    def boom(key):
        raise ConnectionError("blip")

    r.hgetall = boom
    clock.t = 10
    assert sw.engaged() is True  # does not silently resume crawling


def test_daily_and_per_crawl_budgets(r):
    b = cg.CrawlBudget(client=r, max_requests_per_day=3, crawl_id="job1", per_crawl_max_bytes=1000)
    assert b.charge(requests=1, nbytes=100) is None
    assert b.charge(requests=1, nbytes=100) is None
    assert b.charge(requests=1, nbytes=100) == "daily_requests"
    assert b.exhausted() == "daily_requests"
    other_job = cg.CrawlBudget(client=r, crawl_id="job2", per_crawl_max_bytes=1000)
    assert other_job.charge(nbytes=999) is None
    assert other_job.charge(nbytes=1) == "crawl_bytes"
    assert r.ttl(f"{cg.BUDGET_PREFIX}:crawl:job1:bytes") > 0  # counters expire


def test_unlimited_budget_never_touches_redis():
    class _NoCalls:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected redis call {name}")

    b = cg.CrawlBudget(client=_NoCalls())
    assert b.charge(requests=5, nbytes=5) is None and b.exhausted() is None


def test_guard_reports_reason(r):
    guard = cg.CrawlGuard(cg.KillSwitch(client=r, check_secs=0), cg.CrawlBudget(client=r, max_requests_per_day=1))
    assert guard.block_reason("stage1") is None
    guard.charge(requests=1)
    assert guard.block_reason("stage1") == "budget:daily_requests"
    guard.switch.engage(reason="r", actor="a")
    assert guard.block_reason("stage1") == "kill_switch"


# --- Stage 1 middleware ------------------------------------------------------


class _Engine:
    def __init__(self):
        self.closed = []

    def close_spider(self, spider, reason):
        self.closed.append(reason)


class _Crawler:
    def __init__(self):
        self.engine = _Engine()


def _mw(r, **budget):
    from src.stage1.middlewares.crawl_guard_middleware import CrawlGuardMiddleware

    guard = cg.CrawlGuard(cg.KillSwitch(client=r, check_secs=0), cg.CrawlBudget(client=r, **budget))
    return CrawlGuardMiddleware(crawler=_Crawler(), guard=guard)


def test_stage1_drops_requests_and_closes_spider_once_on_kill_switch(r):
    mw = _mw(r)
    req = Request("https://www.uconn.edu/")
    assert mw.process_request(req) is None
    cg.KillSwitch(client=r).engage(reason="abuse", actor="oncall")
    for _ in range(3):
        with pytest.raises(IgnoreRequest):
            mw.process_request(req)
    assert mw.crawler.engine.closed == ["kill_switch"]


def test_stage1_charges_bytes_and_stops_on_budget(r):
    mw = _mw(r, max_bytes_per_day=5000)
    req = Request("https://www.uconn.edu/")
    resp = HtmlResponse(req.url, body=b"x" * 3000, request=req)
    mw.process_response(req, resp)
    assert mw.crawler.engine.closed == []
    mw.process_response(req, resp)
    assert mw.crawler.engine.closed == ["budget_exceeded:daily_bytes"]
    with pytest.raises(IgnoreRequest):
        mw.process_request(req)


def test_guard_middleware_registered_for_spiders():
    from src.stage1.middlewares.spider_config import get_spider_settings

    mws = get_spider_settings("scout")["DOWNLOADER_MIDDLEWARES"]
    assert mws["src.stage1.middlewares.crawl_guard_middleware.CrawlGuardMiddleware"] < 100


# --- Stage 2 -----------------------------------------------------------------


def test_stage2_defers_urls_while_switch_engaged(r):
    from src.stage2.stage2_worker import Stage2Worker

    w = Stage2Worker.__new__(Stage2Worker)
    w._crawl_guard_obj = cg.CrawlGuard(cg.KillSwitch(client=r, check_secs=0))
    cg.KillSwitch(client=r).engage(reason="abuse", actor="oncall")
    assert w._crawl_guard_reason() == "kill_switch"

    called = []

    async def fake_fetch(*a, **k):
        called.append(a)
        return {}

    w._fetch_with_retries = fake_fetch
    w.semaphore = asyncio.Semaphore(1)
    w._host_slot = lambda domain: asyncio.Semaphore(1)

    class _Backoff:
        def blocked(self, domain):
            return False

    w._backoff = lambda: _Backoff()
    out = asyncio.run(w._analyze_url({"url": "https://www.uconn.edu/a", "url_hash": "h"}))
    assert out == {"url": "https://www.uconn.edu/a", "url_hash": "h", "_deferred": True}
    assert called == []


def test_stage2_guard_failure_never_blocks(r):
    from src.stage2.stage2_worker import Stage2Worker

    w = Stage2Worker.__new__(Stage2Worker)

    class _Broken:
        def block_reason(self, stage=""):
            raise RuntimeError("boom")

    w._crawl_guard_obj = _Broken()
    assert w._crawl_guard_reason() is None


# --- CLI ---------------------------------------------------------------------


def test_cli_parser_has_killswitch_commands():
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "cli.py", "killswitch", "on", "--help"], cwd=root, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0 and "--reason" in out.stdout and "--actor" in out.stdout


def test_status_reports_budget_usage(r):
    guard = cg.CrawlGuard(cg.KillSwitch(client=r), cg.CrawlBudget(client=r, max_requests_per_day=10))
    guard.charge(requests=2)
    st = guard.status()
    assert st["kill_switch"]["engaged"] is False
    assert st["budget"]["usage"]["daily_requests"] == 2
    json.dumps(st)
