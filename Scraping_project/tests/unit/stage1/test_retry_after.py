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


@pytest.mark.parametrize("resp", [_resp(200, "10"), _resp(500, "10"), _resp(404)])
def test_no_change_for_other_statuses(resp):
    mw, slot, _ = _mw()
    mw.process_response(REQ, resp, SPIDER)
    assert slot.delay == 0.25


# --- #194: 429/503 without Retry-After back off exponentially ----------------


@pytest.mark.parametrize("resp", [_resp(429), _resp(503), _resp(429, "junk")])
def test_rate_limit_without_usable_header_backs_off(resp):
    mw, slot, _ = _mw()
    mw.process_response(REQ, resp, SPIDER)
    assert slot.delay == 1.0  # max(0.25 * 2, RATE_LIMIT_BACKOFF_MIN=1)


def test_consecutive_429s_double_the_delay_up_to_the_cap_then_restore():
    mw, slot, clock = _mw()
    mw.backoff_max = 10.0
    delays = []
    for _ in range(6):
        mw.process_response(REQ, _resp(429), SPIDER)
        delays.append(slot.delay)
    assert delays == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]
    clock.t += 10 * 4 - 1  # cooldown = delay x RATE_LIMIT_COOLDOWN_FACTOR (4)
    mw.process_request(REQ, SPIDER)
    assert slot.delay == 10.0
    clock.t += 2
    mw.process_request(REQ, SPIDER)
    assert slot.delay == 0.25  # original delay restored


def test_active_wait_survives_autothrottle_lowering_the_delay():
    """AutoThrottle resets slot.delay (clamped to its max) on every 200 response."""
    mw, slot, clock = _mw()
    mw.process_response(REQ, _resp(429, "20"), SPIDER)
    assert slot.delay == 20
    slot.delay = 1.0  # what AutoThrottle does on the next 200 with AUTOTHROTTLE_MAX_DELAY=1
    mw.process_response(REQ, _resp(200), SPIDER)
    assert slot.delay == 20
    slot.delay = 1.0
    mw.process_request(REQ, SPIDER)
    assert slot.delay == 20
    clock.t += 21
    mw.process_response(REQ, _resp(200), SPIDER)
    assert slot.delay == 0.25


def test_backoff_settings_from_crawler():
    from scrapy.settings import Settings

    crawler = SimpleNamespace(settings=Settings({"AUTOTHROTTLE_MAX_DELAY": 60}))
    mw = RetryAfterMiddleware.from_crawler(crawler)
    assert (mw.backoff_min, mw.backoff_max, mw.cooldown_factor) == (1.0, 60.0, 4.0)
    crawler = SimpleNamespace(settings=Settings({"AUTOTHROTTLE_MAX_DELAY": 1.0}))
    assert RetryAfterMiddleware.from_crawler(crawler).backoff_max == 30.0  # never below 30s
    crawler = SimpleNamespace(settings=Settings({"RATE_LIMIT_BACKOFF_MIN": 0.5, "RATE_LIMIT_BACKOFF_MAX": 90,
                                                 "RATE_LIMIT_COOLDOWN_FACTOR": 2}))
    mw = RetryAfterMiddleware.from_crawler(crawler)
    assert (mw.backoff_min, mw.backoff_max, mw.cooldown_factor) == (0.5, 90.0, 2.0)


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
                time.sleep(0.3)  # a slow 429: the wait must count from its arrival
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
    # Retry-After: 1 counted from the 429's arrival (0.3 s after the hit), not from
    # dispatch; stock Scrapy retries immediately.
    assert r["gap"] >= 1.25, r


STORM = textwrap.dedent('''
    import json, sys, threading, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import scrapy
    from scrapy.crawler import CrawlerProcess

    hits = []
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(time.monotonic())
            if len(hits) <= 3:  # a 429 storm with no Retry-After
                self.send_response(429); self.send_header("Content-Length", "0"); self.end_headers(); return
            body = b"<html><body>ok</body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    class S(scrapy.Spider):
        name = "storm_probe"
        def start_requests(self):
            yield scrapy.Request(base + "/storm", dont_filter=True)
        def parse(self, response):
            pass

    mws = {"src.stage1.middlewares.retry_after_middleware.RetryAfterMiddleware": 560} if sys.argv[1] == "on" else {}
    proc = CrawlerProcess({
        "ROBOTSTXT_OBEY": False, "DOWNLOADER_MIDDLEWARES": mws,
        "DOWNLOAD_DELAY": 0, "RANDOMIZE_DOWNLOAD_DELAY": False,
        "AUTOTHROTTLE_ENABLED": True, "AUTOTHROTTLE_START_DELAY": 0, "AUTOTHROTTLE_MAX_DELAY": 60,
        "RATE_LIMIT_BACKOFF_MIN": 0.3,
        "RETRY_TIMES": 5, "LOG_LEVEL": "ERROR", "TELNETCONSOLE_ENABLED": False,
    })
    proc.crawl(S)
    proc.start()
    print(json.dumps({"attempts": len(hits), "gaps": [b - a for a, b in zip(hits, hits[1:])]}))
''')


def _storm(mode):
    out = subprocess.run([sys.executable, "-c", STORM, mode], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_real_crawl_delay_grows_under_429_storm():
    r = _storm("on")
    assert r["attempts"] == 4  # three 429s, then success
    g = r["gaps"]
    # 0.3s, 0.6s, 1.2s: doubling per consecutive 429, despite AutoThrottle being on.
    assert g[0] >= 0.27 and g[1] >= 0.55 and g[2] >= 1.1, g
    assert g[0] < g[1] < g[2], g


def test_real_crawl_stock_scrapy_retries_a_429_storm_immediately():
    """Control: AutoThrottle alone does not slow down on 429s."""
    r = _storm("off")
    assert r["attempts"] == 4
    assert max(r["gaps"]) < 0.25, r["gaps"]


# --- download-delay jitter must not shorten an enforced wait -----------------


def test_wait_disables_slot_jitter_then_restores_it():
    """RANDOMIZE_DOWNLOAD_DELAY waits uniform(0.5, 1.5) x delay; Retry-After is a floor."""
    from scrapy.core.downloader import Slot

    mw, _, clock = _mw()
    slot = Slot(concurrency=1, delay=0.25, randomize_delay=True)
    mw.crawler.engine.downloader.slots["uconn.edu"] = slot
    mw.process_response(REQ, _resp(429, "10"), SPIDER)
    assert slot.delay == 10.0 and slot.randomize_delay is False
    assert min(slot.download_delay() for _ in range(200)) == 10.0
    clock.t += 5
    slot.randomize_delay = True  # nothing else should flip it back mid-wait
    mw.process_request(REQ, SPIDER)
    assert slot.randomize_delay is False
    clock.t += 6
    mw.process_request(REQ, SPIDER)
    assert slot.delay == 0.25 and slot.randomize_delay is True  # both restored


def test_backoff_also_disables_jitter_and_keeps_original_setting():
    from scrapy.core.downloader import Slot

    mw, _, clock = _mw()
    slot = Slot(concurrency=1, delay=0.25, randomize_delay=False)
    mw.crawler.engine.downloader.slots["uconn.edu"] = slot
    mw.process_response(REQ, _resp(503), SPIDER)
    assert slot.randomize_delay is False
    clock.t += 1000
    mw.process_request(REQ, SPIDER)
    assert slot.randomize_delay is False  # was off before; stays off


def test_wait_is_anchored_to_the_429_not_the_request_dispatch():
    """Slot.lastseen is the dispatch time; a slow 429 must not shorten Retry-After."""
    from scrapy.core.downloader import Slot

    mw, _, _ = _mw()
    mw.wall_clock = lambda: 5000.0
    slot = Slot(concurrency=1, delay=0.0, randomize_delay=False)
    slot.lastseen = 4999.85  # request sent 150 ms before the 429 arrived
    mw.crawler.engine.downloader.slots["uconn.edu"] = slot
    mw.process_response(REQ, _resp(429, "1"), SPIDER)
    assert slot.lastseen == 5000.0
    # Scrapy's _process_queue penalty: delay - now + lastseen
    assert slot.download_delay() - 5000.0 + slot.lastseen == 1.0
