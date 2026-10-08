"""#582: soft-ban / captcha detection, quarantine, and domain backoff."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Request

from src.lakehouse.lakehouse_manager import LakehouseManager
from src.stage1.middlewares.soft_ban_middleware import SoftBanMiddleware
from src.stage2.stage2_worker import Stage2Worker, plan_queue_updates
from src.utils.soft_ban import DomainBackoff, SoftBanDetector

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "soft_ban"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- detector
@pytest.mark.parametrize(
    "fixture,status,expected",
    [
        ("cloudflare_challenge_403.html", 403, "cloudflare_challenge"),
        ("cloudflare_challenge_403.html", 200, "cloudflare_challenge"),
        ("cloudflare_attention_503.html", 503, "cloudflare_challenge"),
        ("recaptcha_200.html", 200, "recaptcha"),
        ("hcaptcha_200.html", 200, "hcaptcha"),
        ("datadome_403.html", 403, "datadome"),
        ("akamai_access_denied_403.html", 403, "akamai_access_denied"),
        ("perimeterx_403.html", 403, "perimeterx"),
        ("generic_unusual_traffic_200.html", 200, "generic_bot_check"),
    ],
)
def test_fixture_pages_are_detected(fixture, status, expected):
    assert SoftBanDetector().detect(status, _page(fixture)) == expected


@pytest.mark.parametrize(
    "fixture,status",
    [
        ("plain_forbidden_403.html", 403),  # ordinary 403 stays a normal HTTP error
        ("article_mentions_captcha_200.html", 200),  # long real article about captchas
        ("recaptcha_200.html", 404),  # other statuses are not body-classified
    ],
)
def test_non_soft_ban_pages_are_not_flagged(fixture, status):
    assert SoftBanDetector().detect(status, _page(fixture)) is None


def test_status_and_header_rules():
    d = SoftBanDetector()
    assert d.detect(429) == "http_429"
    assert d.detect(403, "", {"CF-Mitigated": "challenge"}) == "cloudflare_challenge"
    assert d.detect(200, "<html><p>hello</p></html>") is None
    assert d.detect(403, None) is None


def test_signatures_configurable(monkeypatch):
    monkeypatch.setenv(
        "SOFT_BAN_SIGNATURES", json.dumps({"vendor_x": r"blocked-by-vendor-x", "recaptcha": ""})
    )
    d = SoftBanDetector()
    assert d.detect(403, "<p>blocked-by-vendor-x</p>") == "vendor_x"
    assert d.detect(200, _page("recaptcha_200.html")) is None  # default disabled
    assert SoftBanDetector(signatures={"inline": "zzz-wall"}).detect(503, "zzz-wall") == "inline"


def test_bad_signature_config_is_ignored(monkeypatch):
    monkeypatch.setenv("SOFT_BAN_SIGNATURES", "{not json")
    assert SoftBanDetector().detect(200, _page("hcaptcha_200.html")) == "hcaptcha"
    assert "bad" not in SoftBanDetector(signatures={"bad": "("}).signatures


# ----------------------------------------------------------------- backoff
def test_domain_backoff_trips_and_expires():
    clock = Clock()
    b = DomainBackoff(threshold=3, window=60, cooldown=300, clock=clock)
    assert not b.record("a.edu") and not b.record("a.edu")
    assert not b.blocked("a.edu")
    assert b.record("a.edu")  # third within window trips
    assert b.blocked("a.edu") and not b.blocked("b.edu")
    clock.t += 299
    assert b.blocked("a.edu")
    clock.t += 2
    assert not b.blocked("a.edu")


def test_domain_backoff_window_slides():
    clock = Clock()
    b = DomainBackoff(threshold=2, window=10, cooldown=30, clock=clock)
    b.record("a.edu")
    clock.t += 11
    assert not b.record("a.edu")  # first hit aged out
    assert b.record("a.edu")


# ------------------------------------------------------------------ stage2
def test_soft_ban_rows_retry_instead_of_terminal_fail():
    row = {"url": "u", "has_error": True, "error_code": 403, "error_message": "soft_ban:cloudflare_challenge"}
    plain = {"url": "p", "has_error": True, "error_code": 403, "error_message": "http_error"}
    completed, failed, retrying = plan_queue_updates([row, plain], {}, max_retries=3)
    assert completed == [] and failed == ["p"] and [r["url"] for r in retrying] == ["u"]


@pytest.fixture
def site():
    pages = {
        "/cf": (403, _page("cloudflare_challenge_403.html")),
        "/captcha200": (200, _page("recaptcha_200.html")),
        "/article": (200, _page("article_mentions_captcha_200.html")),
        "/forbidden": (403, _page("plain_forbidden_403.html")),
        "/limited": (429, "slow down"),
    }
    hits: list[str] = []

    async def handler(request):
        hits.append(request.path)
        status, body = pages[request.path]
        return web.Response(status=status, text=body, content_type="text/html")

    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    return app, hits


async def _serve(app):
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def test_stage2_quarantines_soft_bans_and_backs_off_domain(site):
    app, hits = site
    runner, base = await _serve(app)
    try:
        w = Stage2Worker(max_concurrent=1)
        clock = Clock()
        w.domain_backoff = DomainBackoff(threshold=3, window=60, cooldown=300, clock=clock)

        cf = await w._analyze_url({"url": f"{base}/cf", "url_hash": "h1"})
        assert cf["has_error"] and cf["error_code"] == 403
        assert cf["error_message"] == "soft_ban:cloudflare_challenge"

        cap = await w._analyze_url({"url": f"{base}/captcha200", "url_hash": "h2"})
        assert cap["has_error"] and cap["error_message"] == "soft_ban:recaptcha"
        assert cap["text_content"] == ""  # never analysed as content

        ok = await w._analyze_url({"url": f"{base}/article", "url_hash": "h3"})
        assert not ok.get("has_error") and ok["word_count"] > 400

        plain = await w._analyze_url({"url": f"{base}/forbidden", "url_hash": "h4"})
        assert plain["error_message"] == "http_error"

        limited = await w._analyze_url({"url": f"{base}/limited", "url_hash": "h5"})
        assert limited["error_message"] == "soft_ban:http_429"  # 3rd soft ban: trips backoff

        n_hits = len(hits)
        deferred = await w._analyze_url({"url": f"{base}/article", "url_hash": "h6"})
        assert deferred == {"url": f"{base}/article", "url_hash": "h6", "_deferred": True}
        assert len(hits) == n_hits  # not fetched while in cooldown

        clock.t += 301
        again = await w._analyze_url({"url": f"{base}/article", "url_hash": "h6"})
        assert not again.get("has_error")
    finally:
        await runner.cleanup()


def test_stage2_run_never_completes_soft_ban_or_deferred(tmp_path):
    lake = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    urls = ["https://ok.example.edu/1", "https://ban.example.edu/2", "https://cool.example.edu/3"]
    lake._write_sync(
        "stage2_queue",
        [{"url": u, "url_hash": f"h{i}", "status": "pending"} for i, u in enumerate(urls)],
        "append",
    )
    w = Stage2Worker()
    w.delta = lake

    async def fake_analyze(record):
        url = record["url"]
        if "ban." in url:
            return w._soft_ban_record(url, record["url_hash"], 403, "cloudflare_challenge", "ban.example.edu")
        if "cool." in url:
            return {"url": url, "url_hash": record["url_hash"], "_deferred": True}
        return {"url": url, "url_hash": record["url_hash"], "status_code": 200, "word_count": 100,
                "has_error": False, "is_low_quality": False, "is_massive_doc": False}

    w._analyze_url = fake_analyze
    try:
        counts = asyncio.run(w._run_traced())
        assert counts["analyzed"] == 2 and counts["errors"] == 1  # deferred row not counted
        status = {r["url"]: r["status"] for r in lake.read("stage2_queue")}
        assert status == {urls[0]: "completed", urls[1]: "pending", urls[2]: "pending"}
        analysed = {r["url"] for r in lake.read("stage2_page_analysis")}
        assert analysed == {urls[0]}
        errors = lake.read("stage2_errors")
        assert [(r["url"], r["error_message"]) for r in errors] == [(urls[1], "soft_ban:cloudflare_challenge")]
    finally:
        lake.shutdown_event.set()


# ------------------------------------------------------------------ stage1
def _crawler(slot):
    downloader = SimpleNamespace(get_slot_key=lambda req: "ban.example.edu", slots={"ban.example.edu": slot})
    stats = SimpleNamespace(values={}, inc_value=lambda k: stats.values.__setitem__(k, stats.values.get(k, 0) + 1))
    return SimpleNamespace(engine=SimpleNamespace(downloader=downloader), stats=stats)


def _resp(url, status, body):
    return HtmlResponse(url=url, status=status, body=body.encode(), encoding="utf-8", request=Request(url))


def test_stage1_middleware_drops_challenge_and_slows_domain():
    slot = SimpleNamespace(delay=0.5)
    crawler = _crawler(slot)
    clock = Clock()
    mw = SoftBanMiddleware(crawler=crawler, backoff=DomainBackoff(threshold=2, window=60, cooldown=100, clock=clock),
                           slot_delay=30.0)
    url = "https://ban.example.edu/page"
    req = Request(url)

    ok = _resp(url, 200, _page("article_mentions_captcha_200.html"))
    assert mw.process_response(req, ok) is ok

    for _ in range(2):
        with pytest.raises(IgnoreRequest, match="soft_ban:cloudflare_challenge"):
            mw.process_response(req, _resp(url, 403, _page("cloudflare_challenge_403.html")))
    assert slot.delay == 30.0
    assert crawler.stats.values["soft_ban/cloudflare_challenge"] == 2

    mw.process_request(req)
    assert slot.delay == 30.0  # still cooling down
    clock.t += 101
    mw.process_request(req)
    assert slot.delay == 0.5  # restored


def test_stage1_middleware_registered_inside_retry():
    from src.stage1.middlewares.spider_config import get_spider_settings

    dl = get_spider_settings("scout")["DOWNLOADER_MIDDLEWARES"]
    assert dl["src.stage1.middlewares.soft_ban_middleware.SoftBanMiddleware"] < 550
