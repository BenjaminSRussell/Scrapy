"""#378: hidden URLs carry discovery provenance into the lake rows (js queue, offsite candidates, stage1_discovery)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import scrapy
from scrapy.http import HtmlResponse, Request

from src.stage1.experimental.base_spider import BaseSpider
from src.stage1.experimental.deep_dive_spider import DeepDiveSpider
from src.stage1.processors.hidden_url_extractor import METHOD_PRECEDENCE, HiddenURLExtractor

BASE = "https://www.uconn.edu/page"
PAGE = b"""<html><head>
<meta http-equiv="refresh" content="5; url=https://tracker.example.net/go">
<script type="application/ld+json">{"sameAs": "https://twitter.com/uconn", "url": "https://www.uconn.edu/about"}</script>
</head><body>
<iframe src="https://www.youtube.com/embed/xyz"></iframe>
<iframe src="https://calendar.uconn.edu/embed"></iframe>
<div data-url="https://notuconn.edu/x"></div>
<div data-href="https://uconn.edu.evil.com/phish"></div>
<div data-src="/files/hidden-report"></div>
</body></html>"""

EXPECTED_METHOD = {
    "https://tracker.example.net/go": "meta_refresh",
    "https://twitter.com/uconn": "json_ld",
    "https://www.youtube.com/embed/xyz": "iframes",
    "https://notuconn.edu/x": "data_attributes",
    "https://uconn.edu.evil.com/phish": "data_attributes",
    "https://calendar.uconn.edu/embed": "iframes",
    "https://www.uconn.edu/about": "json_ld",
    "https://www.uconn.edu/files/hidden-report": "data_attributes",
}


def _resp(body=PAGE):
    return HtmlResponse(url=BASE, body=body, encoding="utf-8", request=Request(BASE, meta={"depth": 0}))


class _FakeRedis:
    def __init__(self):
        self.members: set[str] = set()

    def sismember(self, key, value):
        return value in self.members

    def sadd(self, key, value):
        if value in self.members:
            return 0
        self.members.add(value)
        return 1


@pytest.fixture
def spider():
    s = DeepDiveSpider()
    s.allowed_domains = ["uconn.edu"]
    s.redis_client = _FakeRedis()
    return s


def _parse(spider):
    with patch("src.stage1.base_spider.BaseSpider.parse", return_value=iter(())):
        return list(spider.parse(_resp()))


def _provenance(out):
    """url -> (discovery_source, discovery_method) for each hidden-URL output, whatever its shape."""
    found = {}
    for o in out:
        if isinstance(o, scrapy.Request):
            found[o.url] = (o.meta["discovery_source"], o.meta["discovery_method"])
        elif isinstance(o, dict) and "target_spider" in o:
            found[o["url"]] = (o["discovery_source"], o["discovery_method"])
        elif type(o).__name__ == "OffsiteCandidateItem":
            found[o["external_url"]] = (o["discovery_source"], o["discovery_method"])
    return found


def test_extractor_reports_one_known_method_per_url_including_offsite():
    results, method_of = HiddenURLExtractor(BASE, allowed_domains=["uconn.edu"]).extract_with_methods(_resp())
    assert {u: m for u, m in method_of.items() if u in EXPECTED_METHOD} == EXPECTED_METHOD
    assert set(method_of) == {u for urls in results.values() for u in urls}
    assert set(method_of.values()) <= set(METHOD_PRECEDENCE)


def test_api_literal_reports_api_endpoints_not_generic_javascript():
    body = b'<html><body><script>fetch("/api/v1/items")</script></body></html>'
    _, method_of = HiddenURLExtractor(BASE).extract_with_methods(_resp(body))
    assert method_of["https://www.uconn.edu/api/v1/items"] == "api_endpoints"


def test_offsite_candidates_carry_provenance(spider):
    prov = _provenance(_parse(spider))
    assert prov["https://tracker.example.net/go"] == ("depth_spider", "meta_refresh")
    assert prov["https://twitter.com/uconn"] == ("depth_spider", "json_ld")
    assert prov["https://www.youtube.com/embed/xyz"] == ("depth_spider", "iframes")
    assert prov["https://notuconn.edu/x"] == ("depth_spider", "data_attributes")


def test_crawled_hidden_urls_carry_provenance(spider):
    prov = _provenance(_parse(spider))
    assert prov["https://calendar.uconn.edu/embed"] == ("depth_spider", "iframes")


def test_js_queue_rows_carry_provenance(spider, monkeypatch):
    js = SimpleNamespace(value_score=80, recommended_spider="js")
    monkeypatch.setattr(spider.url_processor.assessor, "assess_url", lambda **kwargs: js)
    rows = [o for o in _parse(spider) if isinstance(o, dict) and o.get("target_spider") == "javascript"]
    by_url = {r["url"]: (r["discovery_source"], r["discovery_method"]) for r in rows}
    assert by_url["https://calendar.uconn.edu/embed"] == ("depth_spider", "iframes")


def test_discovery_rows_carry_provenance_only_when_routed():
    spider = BaseSpider.__new__(BaseSpider)
    spider.discovered_records = []
    routed_meta = {"discovery_source": "depth_spider", "discovery_method": "iframes"}
    routed = HtmlResponse(
        "https://www.uconn.edu/a",
        body=b"<html></html>",
        encoding="utf-8",
        request=Request("https://www.uconn.edu/a", meta=routed_meta),
    )
    plain = HtmlResponse(
        "https://www.uconn.edu/b",
        body=b"<html></html>",
        encoding="utf-8",
        request=Request("https://www.uconn.edu/b"),
    )

    spider._record_discovery(routed, "h1", 1, 13, False, 0, False)
    spider._record_discovery(plain, "h2", 0, 13, False, 0, False)
    spider._record_non_html(routed, "h3", 1, "application/pdf")

    html_row, plain_row, non_html_row = spider.discovered_records
    assert html_row["discovery_source"] == "depth_spider"
    assert html_row["discovery_method"] == "iframes"
    assert "discovery_method" not in plain_row
    assert "discovery_source" not in plain_row
    assert non_html_row["discovery_method"] == "iframes"
