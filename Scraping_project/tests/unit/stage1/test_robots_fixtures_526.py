"""#526: offline robots.txt fixtures; parser decisions and Crawl-delay asserted.

Uses the parser Scrapy is configured with (Protego, the default) and the project's
crawler User-Agent, so a parser upgrade or UA change that flips a decision fails here.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from scrapy.http import Request
from scrapy.robotstxt import ProtegoRobotParser
from scrapy.utils.test import get_crawler

from src.stage1.middlewares.robots_middleware import PoliteRobotsTxtMiddleware, robots_crawl_delay

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "robots"
UA = "UConn-Discovery-Crawler/1.0"
BASE = "https://example.edu"


def _parser(name: str) -> ProtegoRobotParser:
    return ProtegoRobotParser((FIXTURES / name).read_bytes(), None)


def test_fixture_set_is_complete():
    assert len(list(FIXTURES.glob("*.txt"))) >= 4


def test_project_uses_the_parser_and_user_agent_under_test():
    from scrapy.settings.default_settings import ROBOTSTXT_PARSER

    from src import settings

    assert getattr(settings, "ROBOTSTXT_PARSER", ROBOTSTXT_PARSER) == "scrapy.robotstxt.ProtegoRobotParser"
    assert settings.USER_AGENT == UA


DECISIONS = [
    # catch-all group applies to us; the Googlebot-only group does not
    ("disallow_groups.txt", "/", True),
    ("disallow_groups.txt", "/private/report", False),
    ("disallow_groups.txt", "/tmp/a", False),
    ("disallow_groups.txt", "/nogoogle/", True),
    # longest match wins
    ("allow_override.txt", "/private/x", False),
    ("allow_override.txt", "/private/public/y", True),
    ("allow_override.txt", "/docs", False),
    ("allow_override.txt", "/docs/index.html", True),
    ("allow_override.txt", "/docsx", False),  # prefix match, not path-segment match
    # wildcards and end anchors
    ("wildcard.txt", "/a/b.pdf", False),
    ("wildcard.txt", "/a/b.pdf?x=1", True),  # $ anchors: query string escapes the rule
    ("wildcard.txt", "/page?sessionid=1", False),
    ("wildcard.txt", "/x?a=1&sessionid=2", True),  # rule needs the literal "?sessionid="
    ("wildcard.txt", "/searching", False),
    ("wildcard.txt", "/search-help", True),
    ("wildcard.txt", "/a.html", True),
    # a group naming our product token beats the permissive catch-all
    ("bot_specific.txt", "/", False),
    ("bot_specific.txt", "/anything", False),
    # BOM, CRLF, junk lines and unknown directives don't break parsing
    ("malformed.txt", "/", True),
    ("malformed.txt", "/admin/x", False),
]


@pytest.mark.parametrize(("fixture", "path", "allowed"), DECISIONS)
def test_parser_decision(fixture, path, allowed):
    assert _parser(fixture).allowed(BASE + path, UA) is allowed


def test_googlebot_group_still_applies_to_googlebot():
    assert _parser("disallow_groups.txt").allowed(BASE + "/nogoogle/", "Googlebot") is False


@pytest.mark.parametrize(
    ("fixture", "ua", "expected"),
    [
        ("crawl_delay.txt", UA, 5.0),
        ("crawl_delay.txt", "SlowBot", 600.0),
        ("bot_specific.txt", UA, 30.0),
        ("disallow_groups.txt", UA, None),
        ("malformed.txt", UA, None),
    ],
)
def test_crawl_delay_per_group(fixture, ua, expected):
    assert robots_crawl_delay(_parser(fixture), ua) == expected


def _middleware_with_slot(max_delay: float):
    crawler = get_crawler(settings_dict={"ROBOTSTXT_OBEY": True, "ROBOTS_MAX_CRAWL_DELAY": max_delay, "USER_AGENT": UA})
    slot = SimpleNamespace(delay=0.25)
    downloader = SimpleNamespace(
        slots={"example.edu": slot}, per_slot_settings={}, get_slot_key=lambda request: "example.edu"
    )
    crawler.engine = SimpleNamespace(downloader=downloader)
    return PoliteRobotsTxtMiddleware(crawler), slot, downloader


@pytest.mark.parametrize(
    ("fixture", "ua", "cap", "expected"),
    [
        ("crawl_delay.txt", UA, 60.0, 5.0),
        ("bot_specific.txt", UA, 60.0, 30.0),
        ("crawl_delay.txt", "SlowBot", 60.0, 60.0),  # 600s request capped at ROBOTS_MAX_CRAWL_DELAY
    ],
)
def test_middleware_applies_capped_crawl_delay_to_slot(fixture, ua, cap, expected):
    mw, slot, downloader = _middleware_with_slot(cap)
    request = Request(BASE + "/", headers={"User-Agent": ua})
    mw._apply_crawl_delay(_parser(fixture), request, SimpleNamespace(name="scout"))
    assert slot.delay == expected
    assert downloader.per_slot_settings["example.edu"]["delay"] == expected
