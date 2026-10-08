"""#188: Stage 1 waits at least Retry-After (capped) after 429/503."""

import json
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from scrapy.http import Request, Response

from src.stage1.middlewares.retry_after_middleware import RetryAfterMiddleware, parse_retry_after

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "value,expected",
    [("7", 7.0), (b"2.5", 2.5), ("-3", 0.0), ("", None), (None, None), ("soon", None),
     (format_datetime(NOW + timedelta(seconds=30), usegmt=True), 30.0),
     (format_datetime(NOW - timedelta(seconds=30), usegmt=True), 0.0)],
)
def test_parse_retry_after(value, expected):
    assert parse_retry_after(value, now=NOW) == expected


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _mw(max_delay=120.0):
    slot = SimpleNamespace(delay=0.25)
    downloader = SimpleNamespace(get_slot_key=lambda r: "uconn.edu", slots={"uconn.edu": slot})
    crawler = SimpleNamespace(engine=SimpleNamespace(downloader=downloader), stats=None, spider=None)
    clock = Clock()
    return RetryAfterMiddleware(crawler, max_delay=max_delay, clock=clock), slot, clock


def _resp(status, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return Response("https://uconn.edu/a", status=status, headers=headers)


REQ = Request("https://uconn.edu/a")
SPIDER = SimpleNamespace(name="scout")


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_raises_slot_delay_then_restores(status):
    mw, slot, clock = _mw()
    out = mw.process_response(REQ, _resp(status, "10"), SPIDER)
    assert out.status == status  # response passes through untouched
    assert slot.delay == 10.0
    clock.t += 5
    mw.process_request(REQ, SPIDER)
    assert slot.delay == 10.0  # still inside the window
    clock.t += 6
    mw.process_request(REQ, SPIDER)
    assert slot.delay == 0.25  # restored


def test_retry_after_is_capped():
    mw, slot, _ = _mw(max_delay=30)
    mw.process_response(REQ, _resp(429, "3600"), SPIDER)
    assert slot.delay == 30


@pytest.mark.parametrize("resp", [_resp(429), _resp(200, "10"), _resp(500, "10"), _resp(429, "junk")])
def test_no_change_without_usable_header_or_status(resp):
    mw, slot, _ = _mw()
    mw.process_response(REQ, resp, SPIDER)
    assert slot.delay == 0.25


def test_never_lowers_an_existing_higher_delay():
    mw, slot, clock = _mw()
    slot.delay = 45.0  # e.g. a Crawl-delay or soft-ban backoff
    mw.process_response(REQ, _resp(429, "5"), SPIDER)
    assert slot.delay == 45.0
    clock.t += 10
    mw.process_request(REQ, SPIDER)
    assert slot.delay == 45.0


def test_registered_ahead_of_retry_middleware():
    from src import settings
    from src.stage1.middlewares.spider_config import get_spider_settings

    name = "src.stage1.middlewares.retry_after_middleware.RetryAfterMiddleware"
    assert get_spider_settings("scout")["DOWNLOADER_MIDDLEWARES"][name] == 560
    assert settings.DOWNLOADER_MIDDLEWARES[name] == 560  # > RetryMiddleware 550: sees responses first


CRAWL = textwrap.dedent('''
    import json, threading, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import scrapy
    from scrapy.crawler import CrawlerProcess

    hits = []
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, time.monotonic()))
            first = sum(1 for p, _ in hits if p == self.path) == 1
            if self.path == "/limited" and first:
                self.send_response(429); self.send_header("Retry-After", "1")
                self.send_header("Content-Length", "0"); self.end_headers(); return
            body = b"<html><body>ok</body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    class S(scrapy.Spider):
        name = "retry_after_probe"
        def start_requests(self):
            yield scrapy.Request(base + "/limited", dont_filter=True)
        def parse(self, response):
            pass

    proc = CrawlerProcess({
        "ROBOTSTXT_OBEY": False,
        "DOWNLOADER_MIDDLEWARES": {"src.stage1.middlewares.retry_after_middleware.RetryAfterMiddleware": 560},
        "DOWNLOAD_DELAY": 0, "RANDOMIZE_DOWNLOAD_DELAY": False, "AUTOTHROTTLE_ENABLED": False,
        "RETRY_TIMES": 2, "LOG_LEVEL": "ERROR", "TELNETCONSOLE_ENABLED": False,
    })
    proc.crawl(S)
    proc.start()
    times = [t for p, t in hits if p == "/limited"]
    print(json.dumps({"attempts": len(times), "gap": times[1] - times[0] if len(times) > 1 else None}))
''')


def test_real_crawl_retry_waits_for_retry_after():
    out = subprocess.run([sys.executable, "-c", CRAWL], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["attempts"] == 2  # 429, then the retry succeeded
    assert r["gap"] >= 0.9  # waited ~Retry-After: 1 (stock Scrapy retries immediately)
