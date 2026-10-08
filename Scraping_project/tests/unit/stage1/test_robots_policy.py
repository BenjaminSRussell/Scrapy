"""#186 robots.txt is obeyed by default; #188 Crawl-delay is honoured (capped)."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from src.stage1.middlewares.robots_middleware import PoliteRobotsTxtMiddleware, robots_crawl_delay

ROOT = Path(__file__).resolve().parents[3]
OURS = "src.stage1.middlewares.robots_middleware.PoliteRobotsTxtMiddleware"
SCRAPYS = "scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware"


def test_project_settings_obey_robots_by_default():
    from src import settings

    assert settings.ROBOTSTXT_OBEY is True
    assert settings.DOWNLOADER_MIDDLEWARES[OURS] == 100
    assert settings.DOWNLOADER_MIDDLEWARES[SCRAPYS] is None


def test_scout_spider_settings_obey_robots():
    from src.stage1.middlewares.spider_config import get_spider_settings

    s = get_spider_settings("scout")
    assert s["ROBOTSTXT_OBEY"] is True
    assert s["DOWNLOADER_MIDDLEWARES"][OURS] == 100 and s["DOWNLOADER_MIDDLEWARES"][SCRAPYS] is None
    assert "src.stage1.middlewares.soft_ban_middleware.SoftBanMiddleware" in s["DOWNLOADER_MIDDLEWARES"]


def _parser(body: str):
    from scrapy.robotstxt import ProtegoRobotParser

    return ProtegoRobotParser(body.encode(), None)


@pytest.mark.parametrize(
    "body,ua,expected",
    [
        ("User-agent: *\nCrawl-delay: 5\n", "UConn-Discovery-Crawler/1.0", 5.0),
        ("User-agent: UConn-Discovery-Crawler\nCrawl-delay: 2.5\nUser-agent: *\nCrawl-delay: 9\n",
         "UConn-Discovery-Crawler/1.0", 2.5),
        ("User-agent: *\nDisallow: /x\n", "any", None),
        ("", "any", None),
    ],
)
def test_crawl_delay_parsing(body, ua, expected):
    assert robots_crawl_delay(_parser(body), ua) == expected


def test_parser_without_crawl_delay_support():
    assert robots_crawl_delay(object(), "ua") is None


# ------------------------------------------------------- real crawl (subprocess)
CRAWL = textwrap.dedent('''
    import json, sys, threading, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import scrapy
    from scrapy.crawler import CrawlerProcess

    ROBOTS = sys.argv[1]
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, time.monotonic()))
            if self.path == "/robots.txt":
                if ROBOTS == "404":
                    self.send_response(404); self.end_headers(); return
                body = ROBOTS.encode()
                ctype = "text/plain"
            else:
                body = b"<html><body><p>ok</p></body></html>"
                ctype = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    class S(scrapy.Spider):
        name = "robots_probe"
        def start_requests(self):
            for p in ("/public/1", "/private/secret", "/public/2"):
                yield scrapy.Request(base + p, dont_filter=True)
        def parse(self, response):
            pass

    proc = CrawlerProcess({
        "ROBOTSTXT_OBEY": True,
        "ROBOTS_MAX_CRAWL_DELAY": 1.0,
        "USER_AGENT": "UConn-Discovery-Crawler/1.0",
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware": None,
            "src.stage1.middlewares.robots_middleware.PoliteRobotsTxtMiddleware": 100,
        },
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "DOWNLOAD_DELAY": 0,
        "RANDOMIZE_DOWNLOAD_DELAY": False,
        "AUTOTHROTTLE_ENABLED": False,
        "LOG_LEVEL": "WARNING",
        "TELNETCONSOLE_ENABLED": False,
    })
    crawler = proc.create_crawler(S)
    proc.crawl(crawler)
    proc.start()
    stats = crawler.stats.get_stats()
    pages = [(p, t) for p, t in hits if p != "/robots.txt"]
    gaps = [b[1] - a[1] for a, b in zip(pages, pages[1:])]
    print(json.dumps({"paths": [p for p, _ in hits], "gaps": gaps,
                      "forbidden": stats.get("robotstxt/forbidden", 0)}))
''')


def _crawl(robots: str) -> dict:
    out = subprocess.run([sys.executable, "-c", CRAWL, robots], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_disallowed_path_is_never_requested_and_crawl_delay_is_capped():
    r = _crawl("User-agent: *\nDisallow: /private\nCrawl-delay: 5\n")
    assert "/private/secret" not in r["paths"]
    assert r["paths"].count("/robots.txt") == 1  # cached per host
    assert {"/public/1", "/public/2"} <= set(r["paths"])
    assert r["forbidden"] == 1
    # Crawl-delay 5 capped to ROBOTS_MAX_CRAWL_DELAY=1: requests are spaced >= ~1s, not 5s.
    assert r["gaps"] and all(0.9 <= g < 4 for g in r["gaps"])


def test_missing_robots_txt_allows_everything():
    r = _crawl("404")
    assert {"/public/1", "/private/secret", "/public/2"} <= set(r["paths"])
    assert r["forbidden"] == 0
