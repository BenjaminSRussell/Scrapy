"""#388: off-domain hidden URLs never reach the crawl queues; #392: per-category hidden-URL metrics."""

from unittest.mock import patch

import pytest
import scrapy
from prometheus_client import REGISTRY
from scrapy.http import HtmlResponse, Request

from src.stage1.experimental.deep_dive_spider import DeepDiveSpider
from src.stage1.processors.hidden_url_extractor import HiddenURLExtractor

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

OFFSITE = {
    "https://tracker.example.net/go",
    "https://twitter.com/uconn",
    "https://www.youtube.com/embed/xyz",
    "https://notuconn.edu/x",
    "https://uconn.edu.evil.com/phish",
}


def _resp():
    return HtmlResponse(url=BASE, body=PAGE, encoding="utf-8", request=Request(BASE, meta={"depth": 0}))


def _crawlable(results):
    return {u for cat, urls in results.items() if cat != "offsite" for u in urls}


def test_extractor_moves_off_domain_urls_out_of_crawlable_categories():
    results = HiddenURLExtractor(BASE, allowed_domains=["uconn.edu"]).extract_all_hidden_urls(_resp())
    assert set(results["offsite"]) == OFFSITE
    crawlable = _crawlable(results)
    assert not crawlable & OFFSITE
    assert {"https://calendar.uconn.edu/embed", "https://www.uconn.edu/files/hidden-report"} <= crawlable


def test_extractor_without_allowlist_is_unchanged():
    results = HiddenURLExtractor(BASE).extract_all_hidden_urls(_resp())
    assert "offsite" not in results
    assert OFFSITE <= _crawlable(results)  # what main did: offsite mixed into crawl categories


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://uconn.edu/a", True),
        ("https://www.cs.uconn.edu/a", True),
        ("https://notuconn.edu/a", False),
        ("https://uconn.edu.evil.com/a", False),
        ("not a url", False),
    ],
)
def test_scope_is_exact_or_subdomain(url, ok):
    assert HiddenURLExtractor(BASE, allowed_domains=["uconn.edu"]).is_in_scope(url) is ok


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


def _sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_deep_dive_never_queues_offsite_hidden_urls(spider):
    out = _parse(spider)
    queued = {o.url for o in out if isinstance(o, scrapy.Request)} | {
        o["url"] for o in out if isinstance(o, dict) and "target_spider" in o
    }
    assert not queued & OFFSITE
    offsite_items = {o["external_url"] for o in out if type(o).__name__ == "OffsiteCandidateItem"}
    assert offsite_items == OFFSITE  # recorded for triage instead
    assert "https://calendar.uconn.edu/embed" in queued


def test_offsite_recording_can_be_disabled(spider):
    spider.record_offsite = False
    out = _parse(spider)
    assert not [o for o in out if type(o).__name__ == "OffsiteCandidateItem"]


def test_category_and_route_metrics(spider):
    found_before = _sample("scrapy_hidden_urls_found_total", spider="deep_dive", category="iframes")
    offsite_before = _sample("scrapy_hidden_urls_routed_total", spider="deep_dive", route="offsite")
    crawl_before = _sample("scrapy_hidden_urls_routed_total", spider="deep_dive", route="depth_crawl")
    _parse(spider)
    assert _sample("scrapy_hidden_urls_found_total", spider="deep_dive", category="iframes") == found_before + 1
    assert _sample("scrapy_hidden_urls_routed_total", spider="deep_dive", route="offsite") == offsite_before + len(OFFSITE)
    assert _sample("scrapy_hidden_urls_routed_total", spider="deep_dive", route="depth_crawl") > crawl_before

    dup_before = _sample("scrapy_hidden_urls_routed_total", spider="deep_dive", route="duplicate")
    _parse(spider)  # same page again: everything already claimed
    assert _sample("scrapy_hidden_urls_routed_total", spider="deep_dive", route="duplicate") > dup_before
