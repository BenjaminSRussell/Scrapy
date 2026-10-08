"""#662: binary and non-HTML responses never reach HTML parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from scrapy.http import HtmlResponse, Request, Response, TextResponse
from scrapy.responsetypes import responsetypes

from src.stage1.content_policy import classify, classify_response, media_type

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "content_types"


def body(name: str) -> bytes:
    return (FIX / name).read_bytes()


def make_response(url: str, content_type, data: bytes) -> Response:
    """Build the Response subclass Scrapy itself would pick for these headers/body."""
    headers = {} if content_type is None else {"Content-Type": content_type}
    cls = responsetypes.from_args(headers=headers, url=url, body=data)
    return cls(url=url, headers=headers, body=data, request=Request(url))


CASES = [
    # (fixture, content-type header, parse?, reason)
    ("page.html", "text/html; charset=utf-8", True, "html"),
    ("page.html", "TEXT/HTML ; Charset=UTF-8", True, "html"),           # malformed casing/spacing
    ("page.html", b"text/html;;;charset=\xff", True, "html"),           # undecodable bytes
    ("page.html", "text/html, text/plain", True, "html"),               # duplicated header joined
    ("page.xhtml", "application/xhtml+xml", True, "html"),
    ("page.html", None, True, "sniffed_html"),                          # missing header, HTML body
    ("page.html", "", True, "sniffed_html"),
    ("utf16.html", "text/html; charset=utf-16", True, "html"),         # NULs but UTF-16 BOM
    ("doc.pdf", "application/pdf", False, "non_html:application/pdf"),
    ("pixel.png", "image/png", False, "non_html:image/png"),
    ("photo.jpg", "image/jpeg", False, "non_html:image/jpeg"),
    ("archive.zip", "application/zip", False, "non_html:application/zip"),
    ("plain.txt", "text/plain", False, "non_html:text/plain"),
    ("doc.pdf", "text/html", False, "binary_body_mislabeled"),           # lying header
    ("pixel.png", "text/html; charset=utf-8", False, "binary_body_mislabeled"),
    ("archive.zip", "application/xhtml+xml", False, "binary_body_mislabeled"),
    ("data.gz", "text/html", False, "binary_body_mislabeled"),
    ("nul_bytes.bin", "text/html", False, "binary_body_mislabeled"),
    ("doc.pdf", None, False, "binary_body"),                            # missing header, binary
    ("nul_bytes.bin", "", False, "binary_body"),
    ("plain.txt", None, False, "missing_content_type"),                 # explicit, not silent
    ("plain.txt", "garbage;;;", False, "non_html:garbage"),
]


@pytest.mark.parametrize(("fixture", "ctype", "parse", "reason"), CASES)
def test_policy_matrix(fixture, ctype, parse, reason):
    d = classify(ctype, body(fixture))
    assert (d.parse_html, d.reason) == (parse, reason)


@pytest.mark.parametrize("ctype", ["text/html", None, "application/pdf"])
def test_empty_body_is_explicit(ctype):
    for data in (b"", b"   \r\n"):
        assert classify(ctype, data).reason == "empty_body"


def test_media_type_normalisation():
    assert media_type(b"Text/HTML; charset=UTF-8") == "text/html"
    assert media_type(" application/xhtml+xml ;q=1") == "application/xhtml+xml"
    assert media_type(None) == ""


def test_non_text_scrapy_response_never_parsed():
    r = Response("https://uconn.edu/x", headers={"Content-Type": "text/html"}, body=body("page.html"))
    assert classify_response(r).reason == "not_text_response"


# --- the spiders: no binary payload reaches HTML extraction ------------------


@pytest.fixture
def scout(monkeypatch):
    from src.stage1.scout_spider import ScoutSpider

    spider = ScoutSpider.__new__(ScoutSpider)  # skip I/O-heavy __init__
    spider.name = "scout"
    calls: list[str] = []

    def extract(response):
        calls.append(response.url)
        assert isinstance(response, TextResponse)
        assert not response.body.lstrip().startswith((b"%PDF", b"PK\x03\x04", b"\x89PNG"))
        return []

    monkeypatch.setattr(spider, "_extract_urls", extract, raising=False)
    monkeypatch.setattr(spider, "_record_discovery", lambda **k: None, raising=False)
    monkeypatch.setattr(spider, "_hash_url", lambda u: "h", raising=False)
    return spider, calls


@pytest.mark.parametrize(("fixture", "ctype", "parse", "reason"), CASES)
def test_scout_parse_short_circuits(scout, fixture, ctype, parse, reason):
    spider, calls = scout
    url = f"https://uconn.edu/{fixture}"
    response = make_response(url, ctype, body(fixture))
    try:
        list(spider.parse(response) or [])
    except Exception:
        if not parse:
            raise  # a skipped response must short-circuit cleanly
    assert (url in calls) is parse


@pytest.mark.parametrize(("fixture", "ctype", "parse", "reason"), CASES)
def test_base_spider_routes_non_html_to_resource_records(monkeypatch, fixture, ctype, parse, reason):
    from src.stage1.experimental.base_spider import BaseSpider as Base

    spider = Base.__new__(Base)
    spider.name = "base"
    recorded, parsed = [], []
    monkeypatch.setattr(spider, "_hash_url", lambda u: "h", raising=False)
    monkeypatch.setattr(spider, "_categorize_resource", lambda u, c: "x", raising=False)
    monkeypatch.setattr(spider, "_record_non_html", lambda r, h, d, c: recorded.append(r.url), raising=False)

    def detect(response):
        parsed.append(response.url)
        raise StopIteration  # stop right after the HTML gate

    monkeypatch.setattr(spider, "_detect_js_requirement", detect, raising=False)
    url = f"https://uconn.edu/{fixture}"
    response = make_response(url, ctype, body(fixture))
    try:
        results = spider.parse(response)
    except StopIteration:
        results = None
    assert (url in parsed) is parse
    if not parse:
        assert recorded == [url]  # routed as a non-HTML resource, not dropped silently
        assert results[0]["discovery_type"] == "resource"
