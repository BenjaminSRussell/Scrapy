"""#26: the scout adapts to site sections that mostly produce low-confidence URLs."""

from unittest.mock import patch

import pytest
import scrapy
from prometheus_client import REGISTRY
from scrapy.http import HtmlResponse, Request

from src.stage1.scout_spider import ScoutSpider
from src.stage1.section_noise import SectionNoiseTracker, looks_like_trap, section_of


class FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


# ------------------------------------------------------------------ helpers
@pytest.mark.parametrize(
    "url",
    [
        "https://events.uconn.edu/calendar/2024/10/08/",
        "https://events.uconn.edu/calendar/2024/10",
        "https://uconn.edu/news?sort=date",
        "https://uconn.edu/search?q=a&page=2&type=b&lang=en",
        "https://uconn.edu/list?PHPSESSID=abc",
        "https://uconn.edu/a/b/a/b/a/b",
        "https://uconn.edu/x?filter=all",
    ],
)
def test_traps_are_low_confidence(url):
    assert looks_like_trap(url)
    assert SectionNoiseTracker().is_low_value(url)


@pytest.mark.parametrize(
    "url",
    ["https://uconn.edu/academics/programs", "https://uconn.edu/news?id=42", "https://uconn.edu/research/2024-report"],
)
def test_normal_pages_are_not_traps(url):
    assert not looks_like_trap(url)
    assert not SectionNoiseTracker().is_low_value(url)


def test_assessor_low_score_counts_as_low_confidence():
    assert SectionNoiseTracker().is_low_value("https://uconn.edu/login/reset/a/b/c/d")


def test_section_key():
    assert section_of("https://Events.UConn.edu/Calendar/2024/10?x=1") == "events.uconn.edu/calendar"
    assert section_of("https://uconn.edu/") == "uconn.edu/"


def test_tracker_marks_noisy_after_min_pages_and_recovers():
    t = SectionNoiseTracker(min_pages=3, low_value_ratio=0.6, follow_cap=2)
    for _ in range(2):
        t.record_page("s", links=10, low=9)
    assert not t.is_noisy("s")  # not enough evidence yet
    t.record_page("s", links=10, low=9)
    assert t.is_noisy("s")
    assert not t.should_follow("s", "u", followed_so_far=0, low_value=True)
    assert t.should_follow("s", "u", followed_so_far=1, low_value=False)
    assert not t.should_follow("s", "u", followed_so_far=2, low_value=False)  # cap
    for _ in range(6):
        t.record_page("s", links=10, low=0)
    assert not t.is_noisy("s")  # cleaner pages bring it back


def test_disabled_tracker_never_restricts():
    t = SectionNoiseTracker(enabled=False, min_pages=1)
    t.record_page("s", links=10, low=10)
    assert not t.is_noisy("s")
    assert t.should_follow("s", "u", 999, True)


def test_from_config():
    t = SectionNoiseTracker.from_config(
        FakeConfig({"stage1.noisy_sections": {"enabled": "false", "min_pages": 2, "low_value_ratio": 0.5, "follow_cap": 3}})
    )
    assert (t.enabled, t.min_pages, t.low_value_ratio, t.follow_cap) == (False, 2, 0.5, 3)
    assert SectionNoiseTracker.from_config(FakeConfig({})).enabled is True


def test_repo_config_block():
    from src.core.config import get_config

    block = get_config().get("stage1.noisy_sections")
    assert block["enabled"] is True and block["min_pages"] >= 1 and 0 < block["low_value_ratio"] <= 1


# ------------------------------------------------------------------ scout
@pytest.fixture
def spider():
    with patch("src.stage1.scout_spider.get_delta_manager"), \
            patch.object(ScoutSpider, "_discover_and_add_sitemap_urls"):
        s = ScoutSpider()
    s.expand_seeds = False
    s._noise_tracker = SectionNoiseTracker(min_pages=3, low_value_ratio=0.6, follow_cap=2)
    return s


def _page(url, hrefs):
    links = "".join(f"<a href='{h}'>l</a>" for h in hrefs)
    body = f"<html><body><p>text</p>{links}</body></html>".encode()
    return HtmlResponse(url=url, body=body, headers={"Content-Type": "text/html; charset=utf-8"},
                        encoding="utf-8", request=Request(url, meta={"depth": 0}))


def _requests(spider, response):
    with patch.object(spider, "_deduplicate_urls", side_effect=lambda urls: (list(urls), [])):
        return [r for r in spider.parse(response) if isinstance(r, scrapy.Request)]


def _calendar_page(i):
    dated = [f"https://events.uconn.edu/calendar/2024/10/{d:02d}/" for d in range(1, 11)]
    clean = [f"https://events.uconn.edu/calendar/talk-{i}-{k}" for k in range(4)]
    return _page(f"https://events.uconn.edu/calendar/p{i}", dated + clean)


def test_scout_stops_following_trap_links_in_noisy_section(spider):
    first = _requests(spider, _calendar_page(0))
    assert len(first) == 14  # not noisy yet: everything followed
    for i in range(1, 3):
        _requests(spider, _calendar_page(i))
    assert spider._noise_tracker.is_noisy("events.uconn.edu/calendar")

    skips = REGISTRY.get_sample_value("stage1_noisy_section_skips_total") or 0.0
    later = _requests(spider, _calendar_page(9))
    urls = [r.url for r in later]
    assert not any("/2024/10/" in u for u in urls)  # traps dropped
    assert len(urls) == 2  # clean links capped at follow_cap
    assert all(r.priority < 0 for r in later)  # and deprioritised
    assert (REGISTRY.get_sample_value("stage1_noisy_section_skips_total") or 0.0) - skips == 12


def test_clean_sections_are_unaffected(spider):
    for i in range(3):
        _requests(spider, _calendar_page(i))
    clean = [f"https://www.uconn.edu/academics/program-{k}" for k in range(8)]
    out = _requests(spider, _page("https://www.uconn.edu/academics/list", clean))
    assert sorted(r.url for r in out) == sorted(clean)
    assert all(r.priority == 0 for r in out)


def test_disabled_by_config_follows_everything(spider):
    spider._noise_tracker = SectionNoiseTracker(enabled=False, min_pages=1)
    for i in range(5):
        out = _requests(spider, _calendar_page(i))
        assert len(out) == 14
