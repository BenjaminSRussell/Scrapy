"""Large-document timeouts (#396) and config-driven, scoped cookies (#395)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scrapy import Request, Spider
from scrapy.downloadermiddlewares.cookies import CookiesMiddleware
from scrapy.downloadermiddlewares.downloadtimeout import DownloadTimeoutMiddleware
from scrapy.http import HtmlResponse
from scrapy.utils.test import get_crawler

from src.stage1.middlewares.fetch_policy_middleware import (
    FetchPolicyMiddleware,
    host_allowed,
    url_extension,
)

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config.yml"


def chain(settings: dict):
    """FetchPolicy (340) then Scrapy's DownloadTimeout (350) and Cookies (700)."""
    # Project settings.py pins COOKIES_ENABLED from config (default False);
    # Scrapy's own default is True, so mirror the project default here.
    crawler = get_crawler(Spider, settings_dict={"COOKIES_ENABLED": False, **settings})
    spider = Spider("t")
    policy = FetchPolicyMiddleware.from_crawler(crawler)
    timeout = DownloadTimeoutMiddleware.from_crawler(crawler)
    timeout.spider_opened(spider)
    cookies = CookiesMiddleware.from_crawler(crawler) if crawler.settings.getbool("COOKIES_ENABLED") else None

    def run(request: Request) -> Request:
        policy.process_request(request, spider)
        timeout.process_request(request, spider)
        if cookies is not None:
            cookies.process_request(request, spider)
        return request

    return policy, cookies, run


BASE = {"DOWNLOAD_TIMEOUT": 10, "DOWNLOAD_MAXSIZE": 10 * 1024 * 1024}


def test_pdf_gets_extended_timeout_and_html_keeps_the_short_one():
    _, _, run = chain({**BASE, "LARGE_DOC_DOWNLOAD_TIMEOUT": 120, "LARGE_DOC_MAXSIZE": 100 * 1024 * 1024})

    pdf = run(Request("https://catalog.uconn.edu/files/2026-catalog.pdf"))
    html = run(Request("https://catalog.uconn.edu/programs/"))

    assert pdf.meta["download_timeout"] == 120
    assert pdf.meta["download_maxsize"] == 100 * 1024 * 1024
    assert html.meta["download_timeout"] == 10
    assert "download_maxsize" not in html.meta  # global DOWNLOAD_MAXSIZE applies


@pytest.mark.parametrize(
    "url, large",
    [
        ("https://x.uconn.edu/a/REPORT.PDF", True),
        ("https://x.uconn.edu/a/slides.pptx?download=1#p2", True),
        ("https://x.uconn.edu/a/data.xlsx", True),
        ("https://x.uconn.edu/view?file=report.pdf", False),  # extension in the query only
        ("https://x.uconn.edu/pdf/", False),
        ("https://x.uconn.edu/a/page.html", False),
        ("https://x.uconn.edu/a/v1.2", False),
    ],
)
def test_detection_uses_the_url_path_extension(url, large):
    policy, _, _ = chain(BASE)
    assert policy.is_large_document(Request(url)) is large


def test_url_patterns_and_meta_flag_mark_extensionless_documents():
    policy, _, run = chain({**BASE, "LARGE_DOC_URL_PATTERNS": [r"/bitstream/", r"/download\?id="]})
    assert run(Request("https://dc.uconn.edu/bitstream/handle/123")).meta["download_timeout"] == 120
    assert run(Request("https://x.uconn.edu/download?id=9")).meta["download_timeout"] == 120
    assert run(Request("https://x.uconn.edu/doc", meta={"large_document": True})).meta["download_timeout"] == 120
    assert run(Request("https://x.uconn.edu/about")).meta["download_timeout"] == 10


def test_explicit_request_meta_wins():
    _, _, run = chain(BASE)
    req = run(Request("https://x.uconn.edu/a.pdf", meta={"download_timeout": 5, "download_maxsize": 1}))
    assert req.meta["download_timeout"] == 5
    assert req.meta["download_maxsize"] == 1


def test_large_doc_limits_never_undercut_the_global_ones():
    # Large timeout/maxsize below the global values: keep the global values.
    _, _, run = chain(
        {"DOWNLOAD_TIMEOUT": 200, "DOWNLOAD_MAXSIZE": 500, "LARGE_DOC_DOWNLOAD_TIMEOUT": 60, "LARGE_DOC_MAXSIZE": 100}
    )
    req = run(Request("https://x.uconn.edu/a.pdf"))
    assert req.meta["download_timeout"] == 200
    assert req.meta["download_maxsize"] == 500
    # Unlimited global maxsize stays unlimited.
    _, _, run = chain({"DOWNLOAD_MAXSIZE": 0})
    assert "download_maxsize" not in run(Request("https://x.uconn.edu/a.pdf")).meta


def test_stats_count_large_documents():
    policy, _, run = chain(BASE)
    run(Request("https://x.uconn.edu/a.pdf"))
    run(Request("https://x.uconn.edu/b.docx"))
    run(Request("https://x.uconn.edu/c"))
    assert policy.stats.get_value("fetch_policy/large_document") == 2


# --- cookies (#395) -------------------------------------------------------


def _set_cookie_then_follow(cookies: CookiesMiddleware, run, url: str) -> Request:
    first = run(Request(url))
    response = HtmlResponse(url, headers={"Set-Cookie": "session=abc; Path=/"}, body=b"", request=first)
    cookies.process_response(first, response, Spider("t"))
    return run(Request(url + "next"))


def test_cookies_disabled_sends_nothing():
    policy, cookies, run = chain(BASE)
    assert cookies is None
    assert policy.cookies_enabled is False
    req = run(Request("https://login.uconn.edu/"))
    assert b"Cookie" not in req.headers


def test_cookie_scope_only_allowlisted_hosts_get_a_jar():
    _, cookies, run = chain({**BASE, "COOKIES_ENABLED": True, "COOKIES_ALLOWED_DOMAINS": ["portal.uconn.edu"]})

    allowed = _set_cookie_then_follow(cookies, run, "https://portal.uconn.edu/")
    sub = _set_cookie_then_follow(cookies, run, "https://a.portal.uconn.edu/")
    other = _set_cookie_then_follow(cookies, run, "https://news.uconn.edu/")

    assert allowed.headers.get("Cookie") == b"session=abc"
    assert b"session=abc" in sub.headers.get("Cookie")
    assert other.headers.get("Cookie") is None
    assert other.meta["dont_merge_cookies"] is True


def test_cookies_enabled_with_no_allowlist_is_plain_scrapy_behaviour():
    _, cookies, run = chain({**BASE, "COOKIES_ENABLED": True})
    req = _set_cookie_then_follow(cookies, run, "https://news.uconn.edu/")
    assert req.headers.get("Cookie") == b"session=abc"
    assert "dont_merge_cookies" not in req.meta


@pytest.mark.parametrize(
    "host, ok",
    [
        ("portal.uconn.edu", True),
        ("x.portal.uconn.edu", True),
        ("PORTAL.UCONN.EDU.", True),
        ("evilportal.uconn.edu", False),
        ("portal.uconn.edu.evil.com", False),
        ("", False),
    ],
)
def test_host_allowed_matches_host_and_subdomains_only(host, ok):
    assert host_allowed(host, [".portal.uconn.edu"]) is ok


def test_url_extension_ignores_query_and_fragment():
    assert url_extension("https://a/b/c.PDF?x=1.html#y.doc") == "pdf"
    assert url_extension("https://a/b/") == ""


# --- config wiring ----------------------------------------------------------


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_section(self, name):
        return {}


def test_config_toggle_flips_the_scrapy_cookie_setting():
    from src.settings import derive_scrapy_config

    on = derive_scrapy_config(
        FakeConfig(
            {
                "stage1.fetch_policy.cookies_enabled": True,
                "stage1.fetch_policy.cookies_allowed_domains": ["portal.uconn.edu"],
                "stage1.fetch_policy.large_doc_download_timeout": 300,
            }
        )
    )
    assert on["cookies_enabled"] is True
    assert on["cookies_allowed_domains"] == ["portal.uconn.edu"]
    assert on["large_doc_download_timeout"] == 300
    assert "cookies_enabled" not in derive_scrapy_config(FakeConfig({}))


def test_committed_config_keeps_cookies_off_and_registers_the_middleware():
    raw = yaml.safe_load(REPO_CONFIG.read_text())
    policy = raw["stage1"]["fetch_policy"]
    assert policy["cookies_enabled"] is False
    assert "pdf" in policy["large_doc_extensions"]
    assert policy["large_doc_download_timeout"] > raw["stage1"]["spiders"]["scout"]["download_timeout"]

    from src import settings

    assert settings.COOKIES_ENABLED is False
    assert settings.DOWNLOAD_TIMEOUT < settings.LARGE_DOC_DOWNLOAD_TIMEOUT
    mw = settings.DOWNLOADER_MIDDLEWARES["src.stage1.middlewares.fetch_policy_middleware.FetchPolicyMiddleware"]
    assert mw < 350  # before DownloadTimeoutMiddleware


def test_spider_settings_default_to_cookieless(monkeypatch):
    from src.stage1.middlewares import spider_config

    class Raw:
        def __init__(self, spider):
            self.spider = spider

        def get_raw_config(self):
            return {"stage1": {"spiders": {"s": self.spider}}}

    for spider, expected in (
        ({"depth_limit": 3}, False),
        ({"cookies_enabled": "false"}, False),
        ({"cookies_enabled": True}, True),
    ):
        monkeypatch.setattr(spider_config.Config, "get_instance", classmethod(lambda cls, s=spider: Raw(s)))
        settings = spider_config.get_spider_settings("s")
        assert settings["COOKIES_ENABLED"] is expected
        assert (
            settings["DOWNLOADER_MIDDLEWARES"]["src.stage1.middlewares.fetch_policy_middleware.FetchPolicyMiddleware"]
            == 340
        )
