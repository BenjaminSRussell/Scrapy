"""ScoutSpider.parse against HTML snapshot fixtures (#240, #273).

Offline: Delta is mocked, the Redis seen-set is FakeRedis, and pages come from
``tests/fixtures/html`` through the ``html_response`` fixture.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
import pytest
import scrapy

from src.stage1.scout_spider import ScoutSpider

pytestmark = [pytest.mark.scrapy, pytest.mark.stage1]

BASE = "https://example.com/dept/"


@pytest.fixture
def spider():
    redis_helper = SimpleNamespace(client=fakeredis.FakeRedis())
    with patch("src.stage1.scout_spider.get_delta_manager"), \
            patch("src.stage1.experimental.base_spider.get_redis", return_value=redis_helper):
        sp = ScoutSpider(allowed_domains=["example.com"])
    sp.expand_seeds = False  # no SeedManager writes
    return sp


def _split(outputs):
    requests = [o for o in outputs if isinstance(o, scrapy.Request)]
    js = [o for o in outputs if isinstance(o, dict) and o.get("target_spider") == "javascript"]
    stage2 = [o for o in outputs if isinstance(o, dict) and o.get("target_stage") == "stage2"]
    return requests, js, stage2


@pytest.mark.smoke
def test_simple_page_relative_absolute_and_duplicates(spider, html_response):
    requests, js, stage2 = _split(list(spider.parse(html_response("simple", url=BASE))))
    followed = sorted(r.url for r in requests)
    # Relative links resolve against the page URL; /about, /about (dup) and
    # /about#history collapse to one request.
    assert followed == [
        "https://example.com/about",
        "https://example.com/courses",
        "https://example.com/dept/people/faculty.html",
    ]
    assert len(followed) == len(set(followed))
    assert all(r.meta["depth"] == 1 and r.callback == spider.parse for r in requests)
    # Each followed HTML page is also queued for the JS spider and for Stage 2.
    assert sorted(i["url"] for i in js) == followed
    html_stage2 = sorted(i["url"] for i in stage2 if i["content_hint"] == "html")
    assert html_stage2 == followed
    # The PDF goes to Stage 2 only (no crawl request).
    pdf = [i for i in stage2 if i["url"].endswith("handbook.pdf")]
    assert len(pdf) == 1 and pdf[0]["content_hint"] == "pdf" and pdf[0]["priority"] == 1
    assert not any(r.url.endswith(".pdf") for r in requests)
    # Images, mailto: and javascript: never become requests or queue rows.
    every_url = {r.url for r in requests} | {i["url"] for i in js + stage2}
    assert not any(u.endswith(".png") or u.startswith(("mailto:", "javascript:")) for u in every_url)
    assert all(i["parent_url"] == BASE and i["queued_by"] == "scout" for i in js + stage2)


def test_urls_seen_on_one_page_are_not_requeued_from_another(spider, html_response):
    first = list(spider.parse(html_response("simple", url=BASE)))
    assert first
    again = list(spider.parse(html_response("simple", url="https://example.com/dept/index.html")))
    requests, js, stage2 = _split(again)
    assert requests == [] and js == [] and stage2 == []


def test_nav_heavy_page_follows_every_unique_link(spider, html_response):
    requests, _, _ = _split(list(spider.parse(html_response("nav_heavy", url="https://example.com/"))))
    urls = {r.url for r in requests}
    assert {f"https://example.com/section-{i}" for i in range(1, 31)} <= urls
    assert "https://example.com/news/2026/10/featured-story" in urls
    assert {f"https://example.com/policies/{p}" for p in ("privacy", "accessibility")} <= urls
    assert len(urls) == len(requests)  # no duplicate requests


def test_empty_page_yields_nothing_and_counts_skip(spider, html_response):
    outputs = list(spider.parse(html_response("empty", url="https://example.com/blank")))
    assert outputs == []
    assert sum(spider.skip_counters.values()) >= 1


def test_js_heavy_fixture_is_detected(spider, html_response):
    assert spider._detect_js_requirement(html_response("js_heavy", url="https://example.com/app"))
    assert not spider._detect_js_requirement(html_response("simple", url=BASE))


def test_html_response_fixture_is_scrapy_response(html_response):
    response = html_response("simple", url=BASE, meta={"depth": 3})
    assert isinstance(response, scrapy.http.HtmlResponse)
    assert response.meta["depth"] == 3
    assert response.css("title::text").get() == "Department of Examples"
    with pytest.raises(FileNotFoundError):
        html_response("does-not-exist")
