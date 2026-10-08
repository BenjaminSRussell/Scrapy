"""#199: empty / blank-shell responses never feed Stage 2 or the frontier."""

from unittest.mock import patch

import pytest
from prometheus_client import REGISTRY
from scrapy.http import HtmlResponse, Request, TextResponse

from src.stage1.scout_spider import ScoutSpider


@pytest.fixture
def spider():
    # No network: skip sitemap discovery at construction.
    with patch("src.stage1.scout_spider.get_delta_manager"), \
            patch.object(ScoutSpider, "_discover_and_add_sitemap_urls"):
        s = ScoutSpider()
    s.expand_seeds = False
    return s


def _resp(body: bytes, url="https://www.uconn.edu/page"):
    return HtmlResponse(url=url, body=body, headers={"Content-Type": "text/html; charset=utf-8"},
                        encoding="utf-8", request=Request(url, meta={"depth": 0}))


def _skipped(spider_name="scout"):
    return REGISTRY.get_sample_value("scrapy_urls_skipped_total", {"spider": spider_name, "skip_reason": "empty_body"}) or 0.0


@pytest.mark.parametrize(
    "body",
    [b"", b"   \n\t  ", b"<html></html>", b"<html><head><title></title></head><body>  </body></html>",
     b"<html><body><div><span> </span></div><!-- nothing --></body></html>"],
    ids=["zero-byte", "whitespace", "bare-html", "blank-body", "blank-divs"],
)
def test_empty_bodies_yield_nothing_and_are_counted(spider, body, caplog):
    before = _skipped()
    with patch.object(spider, "_extract_urls") as extract:
        out = list(spider.parse(_resp(body)))
    assert out == []
    extract.assert_not_called()  # never reaches URL extraction / queueing
    assert spider.skip_counters["empty_body"] >= 1
    assert _skipped() == before + 1
    assert any("empty_body" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "body",
    [b"<html><body><p>Admissions</p></body></html>",
     b"<html><body><a href='/next'></a></body></html>",
     b"<html><body><div id='app'></div><script src='/app.js'></script></body></html>"],
    ids=["text-only", "link-only", "js-shell"],
)
def test_pages_with_text_or_refs_are_not_empty(body):
    assert ScoutSpider._empty_body_reason(_resp(body)) is None


def test_real_page_still_queues_links(spider):
    body = b"<html><body><p>Hi</p><a href='https://www.uconn.edu/about'>About</a></body></html>"
    with patch.object(spider, "_deduplicate_urls", side_effect=lambda urls: (list(urls), [])):
        out = list(spider.parse(_resp(body)))
    assert any("uconn.edu/about" in str(getattr(o, "url", o)) for o in out), "normal pages still queue links"
    assert spider.skip_counters.get("empty_body", 0) == 0


@pytest.mark.parametrize("ctype,body", [
    ("text/plain", b"https://uconn.edu/listing.html\n"),
    ("application/json", b'{"next": "https://uconn.edu/a"}'),
    ("application/xml", b"<urlset><url><loc>https://uconn.edu/b</loc></url></urlset>"),
])
def test_non_html_bodies_are_not_treated_as_blank_shells(ctype, body):
    resp = TextResponse(url="https://uconn.edu/feed", body=body, headers={"Content-Type": ctype})
    assert ScoutSpider._empty_body_reason(resp) is None


def test_zero_byte_non_html_is_still_empty():
    resp = TextResponse(url="https://uconn.edu/feed", body=b"  \n", headers={"Content-Type": "text/plain"})
    assert ScoutSpider._empty_body_reason(resp) == "empty_body"
