"""#633: YAML + Redis URL allow/block, hot reload, audit, scout drops before request."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
import pytest
import scrapy
from scrapy.http import HtmlResponse, Request

from src.stage1.scout_spider import ScoutSpider
from src.stage1.url_policy import UrlPolicy, rule_matches


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _Down:
    def smembers(self, key):
        raise ConnectionError("redis down")


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


def test_rule_shapes():
    url = "https://www.example.edu/calendar/2024"
    assert rule_matches("/calendar", url)
    assert rule_matches("/calendar/", url)
    assert not rule_matches("/calendar-archive", url)
    assert rule_matches("example.edu", url)
    assert rule_matches("www.example.edu", url)
    assert not rule_matches("other.edu", url)
    assert rule_matches("example.edu/calendar", url)
    assert not rule_matches("example.edu/secret", url)
    assert rule_matches("https://www.example.edu/calendar", url)
    assert not rule_matches("https://www.example.edu/calendar-archive", url)
    assert not rule_matches("http://www.example.edu/calendar", url)
    assert rule_matches("https://www.example.edu/calendar/*", url)
    assert rule_matches("*/calendar/*", url)
    assert rule_matches(url + "?x=1", url + "?x=1")
    assert not rule_matches("https://www.example.edu/search?q=1", url)


def test_block_wins_and_empty_allow_is_open(redis):
    policy = UrlPolicy(yaml_block=("/secret",), yaml_allow=(), client=redis, reload_seconds=0)
    assert policy.allows("https://example.edu/public") is True
    assert policy.allows("https://example.edu/secret") is False
    assert policy.decision("https://example.edu/secret").reason == "block"

    allow = UrlPolicy(yaml_allow=("/keep",), yaml_block=("/keep/nope",), client=redis, reload_seconds=0)
    assert allow.allows("https://example.edu/keep") is True
    assert allow.allows("https://example.edu/keep/nope") is False
    assert allow.decision("https://example.edu/other").reason == "not_allowlisted"


def test_redis_overlay_hot_reload_within_n_seconds(redis):
    clock = _Clock()
    policy = UrlPolicy(client=redis, reload_seconds=5, clock=clock)
    assert policy.allows("https://example.edu/rush") is True
    redis.sadd("url_policy:block", "/rush")
    clock.t = 4.9
    assert policy.allows("https://example.edu/rush") is True  # still inside the window
    clock.t = 5.0
    decision = policy.decision("https://example.edu/rush")
    assert decision.allowed is False and decision.reason == "block" and decision.rule == "/rush"


def test_redis_blip_keeps_last_overlay_and_yaml_still_applies(redis):
    clock = _Clock()
    flaky = SimpleNamespace(inner=redis, fail=False)

    def smembers(key):
        if flaky.fail:
            raise ConnectionError("refused")
        return redis.smembers(key)

    flaky.smembers = smembers
    policy = UrlPolicy(yaml_block=("/yaml-secret",), client=flaky, reload_seconds=5, clock=clock)
    redis.sadd("url_policy:block", "/redis-secret")
    assert policy.allows("https://example.edu/redis-secret") is False
    flaky.fail = True
    clock.t = 5.0
    assert policy.allows("https://example.edu/redis-secret") is False
    assert policy.allows("https://example.edu/yaml-secret") is False
    assert policy.allows("https://example.edu/public") is True

    down = UrlPolicy(yaml_block=("/secret",), client=_Down(), reload_seconds=0)
    assert down.allows("https://example.edu/secret") is False
    assert down.allows("https://example.edu/ok") is True


def test_audit_log_records_changes_only(redis):
    policy = UrlPolicy(client=redis, reload_seconds=0)
    assert policy.add("block", "/rush", actor="oncall", reason="abuse report") is True
    assert policy.add("block", "/rush", actor="oncall", reason="again") is False
    assert policy.add("allow", "/keep", actor="oncall") is True
    assert policy.remove("block", "/rush", actor="oncall", reason="cleared") is True
    assert policy.remove("block", "/rush", actor="oncall") is False
    audit = policy.audit()
    assert [row["action"] for row in audit] == ["block_remove", "allow_add", "block_add"]
    assert audit[-1]["actor"] == "oncall" and audit[-1]["reason"] == "abuse report" and audit[-1]["at"]
    with pytest.raises(ValueError):
        policy.add("block", "/x", actor="oncall", reason="")
    with pytest.raises(ValueError):
        policy.add("allow", "  ", actor="oncall")
    with pytest.raises(ValueError):
        policy.remove("block", "/x", actor=" ")


def test_shipped_config_is_open_and_reloads_in_five_seconds(redis):
    policy = UrlPolicy.from_config(client=redis)
    assert policy.reload_seconds == 5
    assert policy.yaml_allow == () and policy.yaml_block == ()
    assert policy.allows("https://uconn.edu/research") is True


def _spider(redis):
    # Do not pass start_urls into the constructor: a non-empty list with
    # parse_sitemaps would fetch a sitemap (no live scrape in this test).
    helper = SimpleNamespace(client=redis)
    with patch("src.stage1.scout_spider.get_delta_manager"), patch(
        "src.stage1.experimental.base_spider.get_redis", return_value=helper
    ):
        spider = ScoutSpider(allowed_domains=["example.com"])
    spider.expand_seeds = False
    spider.parse_sitemaps = False
    return spider


def test_scout_drops_blocked_url_before_request_and_enqueue(redis):
    spider = _spider(redis)
    redis.sadd("url_policy:block", "https://example.com/about")
    redis.sadd("url_policy:block", "/courses")
    body = (
        b"<html><body>"
        b'<a href="/about">About</a>'
        b'<a href="/courses">Courses</a>'
        b'<a href="/people">People</a>'
        b'<a href="/handbook.pdf">PDF</a>'
        b"</body></html>"
    )
    page = "https://example.com/dept/"
    response = HtmlResponse(
        url=page,
        body=body,
        encoding="utf-8",
        request=Request(page, meta={"depth": 0}),
    )
    outputs = list(spider.parse(response))
    requests = [item for item in outputs if isinstance(item, scrapy.Request)]
    queued = [item for item in outputs if isinstance(item, dict)]
    assert [item.url for item in requests] == ["https://example.com/people"]
    assert {item["url"] for item in queued} == {"https://example.com/people", "https://example.com/handbook.pdf"}
    assert spider.scout_stats["url_policy_dropped"] >= 2


def test_scout_start_drops_blocked_seed_before_request(redis):
    spider = _spider(redis)
    spider.start_urls = ["https://example.com/ok", "https://example.com/secret"]
    redis.sadd("url_policy:block", "/secret")

    async def _collect():
        return [request async for request in spider.start()]

    requests = asyncio.run(_collect())
    assert [request.url for request in requests] == ["https://example.com/ok"]


def test_cli_url_policy_help_lists_add_and_remove():
    root = Path(__file__).resolve().parents[3]
    out = subprocess.run(
        [sys.executable, "cli.py", "url-policy", "add", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0
    assert "block" in out.stdout and "allow" in out.stdout and "--actor" in out.stdout and "--reason" in out.stdout
