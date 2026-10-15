"""#939: rustmapper Crawl-Delay is inherited by Stage 1 scout after discovery."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scrapy.http import Request

from src.stage1.discovery_handoff import (
    DiscoveryHandoff,
    DiscoveryStatus,
    checksum_urls,
    consume_rustmapper_handoff,
    normalize_crawl_delay_map,
)
from src.stage1.middlewares.host_politeness_middleware import (
    MIDDLEWARE_PATH,
    MIDDLEWARE_PRIORITY,
    DiscoveredCrawlDelayMiddleware,
    inherited_download_delay,
)

HOST = "example.edu"
URL = "https://example.edu/admissions"


def test_envelope_field_is_documented():
    doc = DiscoveryHandoff.__doc__ or ""
    assert "crawl_delay_seconds" in doc
    assert "download delay" in doc


def test_missing_delay_field_is_empty_map():
    handoff = DiscoveryHandoff.from_dict(
        {
            "status": "success",
            "discovered": [URL],
            "discovery_source": "rustmapper",
        }
    )
    assert handoff.crawl_delay_seconds == {}
    assert handoff.to_dict()["crawl_delay_seconds"] == {}


def test_normalize_drops_non_positive_and_keeps_stricter_host():
    parsed = normalize_crawl_delay_map(
        {
            "Example.EDU": 2,
            "example.edu": 4,
            "slow.edu": 0,
            "nope.edu": "later",
            "neg.edu": -3,
            "ok.edu": "2.5",
        }
    )
    assert parsed == {"example.edu": 4.0, "ok.edu": 2.5}


def test_list_form_and_url_host():
    parsed = normalize_crawl_delay_map(
        [{"host": "https://WWW.Example.EDU/robots.txt", "crawl_delay_seconds": "3"}]
    )
    assert parsed == {"www.example.edu": 3.0}


@pytest.mark.parametrize(
    ("configured", "discovered", "cap", "expected"),
    [
        (0.01, None, 60.0, None),
        (0.25, 0, 60.0, None),
        (0.01, 5, 60.0, 5.0),
        (10.0, 3, 60.0, 10.0),
        (0.25, 120, 60.0, 60.0),
        (90.0, 120, 60.0, 90.0),
    ],
)
def test_inherited_delay_is_max_of_configured_and_capped_discovered(configured, discovered, cap, expected):
    assert inherited_download_delay(configured, discovered, cap=cap) == expected


def _middleware(delays, configured=0.25, cap=60.0, slot_delay=0.25):
    slot = SimpleNamespace(delay=slot_delay)
    downloader = SimpleNamespace(
        slots={HOST: slot},
        per_slot_settings={},
        get_slot_key=lambda request: HOST,
    )
    crawler = SimpleNamespace(engine=SimpleNamespace(downloader=downloader), stats=None)
    mw = DiscoveredCrawlDelayMiddleware(
        crawler=crawler,
        delays=delays,
        configured_delay=configured,
        max_crawl_delay=cap,
    )
    return mw, slot, downloader


def test_spider_host_map_is_applied_on_the_first_request():
    mw, slot, _downloader = _middleware({}, configured=0.01, slot_delay=0.01)
    spider = SimpleNamespace(name="scout", host_crawl_delays={"Example.EDU": 4})
    mw.process_request(Request(URL), spider)
    assert slot.delay == 4.0
    assert mw.delays["example.edu"] == 4.0


def test_discovered_crawl_delay_raises_stage1_slot():
    mw, slot, downloader = _middleware({HOST: 5.0}, configured=0.01, slot_delay=0.01)
    mw.process_request(Request(URL), SimpleNamespace(name="scout"))
    assert slot.delay == 5.0
    assert downloader.per_slot_settings[HOST]["delay"] == 5.0


def test_missing_host_keeps_configured_slot_delay():
    mw, slot, downloader = _middleware({"other.edu": 8.0}, configured=0.25, slot_delay=0.25)
    mw.process_request(Request(URL), SimpleNamespace(name="scout"))
    assert slot.delay == 0.25
    assert downloader.per_slot_settings == {}


def test_configured_delay_wins_when_it_is_stricter():
    mw, slot, _downloader = _middleware({HOST: 3.0}, configured=10.0, slot_delay=0.25)
    mw.process_request(Request(URL), SimpleNamespace(name="scout"))
    assert slot.delay == 10.0


def test_spider_download_delay_is_the_configured_floor():
    mw, slot, _downloader = _middleware({HOST: 3.0}, configured=0.01, slot_delay=0.01)
    mw.process_request(Request(URL), SimpleNamespace(name="scout", download_delay=8))
    assert slot.delay == 8.0


def test_cap_limits_discovered_delay_but_not_below_configured():
    mw, slot, _downloader = _middleware({HOST: 120.0}, configured=0.25, cap=2.0, slot_delay=0.25)
    mw.process_request(Request(URL), SimpleNamespace(name="scout"))
    assert slot.delay == 2.0

    mw, slot, _downloader = _middleware({HOST: 120.0}, configured=5.0, cap=2.0, slot_delay=0.25)
    mw.process_request(Request(URL), SimpleNamespace(name="scout"))
    assert slot.delay == 5.0


def test_floor_is_reapplied_when_autothrottle_lowers_the_slot():
    mw, slot, _downloader = _middleware({HOST: 5.0}, configured=0.01, slot_delay=0.01)
    request = Request(URL)
    spider = SimpleNamespace(name="scout")
    mw.process_request(request, spider)
    slot.delay = 0.1
    mw.process_request(request, spider)
    assert slot.delay == 5.0


def test_does_not_lower_a_longer_slot_delay():
    mw, slot, _downloader = _middleware({HOST: 5.0}, configured=0.25, slot_delay=30.0)
    mw.process_request(Request(URL), SimpleNamespace(name="scout"))
    assert slot.delay == 30.0


class _Settings:
    def __init__(self, data: dict):
        self._data = data

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def getfloat(self, key: str, default: float = 0.0) -> float:
        return float(self._data.get(key, default))


def test_from_crawler_reads_handoff_envelope(tmp_path: Path):
    sidecar = tmp_path / "handoff.json"
    sidecar.write_text(
        json.dumps(
            {
                "status": "success",
                "discovered": [URL],
                "discovery_source": "rustmapper",
                "crawl_delay_seconds": {"Example.EDU": 4},
            }
        ),
        encoding="utf-8",
    )
    crawler = SimpleNamespace(
        settings=_Settings(
            {
                "DISCOVERY_HANDOFF_PATH": str(sidecar),
                "DOWNLOAD_DELAY": 0.25,
                "ROBOTS_MAX_CRAWL_DELAY": 60.0,
            }
        )
    )
    mw = DiscoveredCrawlDelayMiddleware.from_crawler(crawler)
    assert mw.delays == {"example.edu": 4.0}
    assert mw.configured_delay == 0.25
    assert mw.max_crawl_delay == 60.0


def test_rustmapper_sidecar_delay_survives_jsonl_rebuild(tmp_path: Path):
    jsonl = tmp_path / "sitemap.jsonl"
    jsonl.write_text(json.dumps({"url": URL, "crawl_delay_seconds": 2}) + "\n", encoding="utf-8")
    sidecar = tmp_path / "handoff.json"
    handoff = DiscoveryHandoff.success(
        [URL],
        discovery_source="rustmapper",
        crawl_delay_seconds={"example.edu": 6, "other.edu": 1},
    )
    # checksum must match the JSONL urls or success rebuild replaces the body
    assert handoff.checksum == checksum_urls([URL])
    sidecar.write_text(json.dumps(handoff.to_dict()), encoding="utf-8")

    loaded = consume_rustmapper_handoff(jsonl, sidecar, site=HOST)
    assert loaded.status == DiscoveryStatus.SUCCESS
    assert loaded.discovered == [URL]
    assert loaded.crawl_delay_seconds["example.edu"] == 6.0
    assert loaded.crawl_delay_seconds["other.edu"] == 1.0


def test_jsonl_host_record_delay_without_urls_is_empty_handoff(tmp_path: Path):
    jsonl = tmp_path / "sitemap.jsonl"
    jsonl.write_text(
        json.dumps({"host": "uconn.edu", "crawl_delay_seconds": 7}) + "\n",
        encoding="utf-8",
    )
    handoff = consume_rustmapper_handoff(jsonl)
    assert handoff.status == DiscoveryStatus.EMPTY
    assert handoff.discovered == []
    assert handoff.crawl_delay_seconds == {"uconn.edu": 7.0}


def test_scout_and_project_register_middleware_before_robots():
    from src import settings
    from src.stage1.middlewares.spider_config import get_spider_settings

    robots = "src.stage1.middlewares.robots_middleware.PoliteRobotsTxtMiddleware"
    for middlewares in (
        settings.DOWNLOADER_MIDDLEWARES,
        get_spider_settings("scout")["DOWNLOADER_MIDDLEWARES"],
    ):
        assert middlewares[MIDDLEWARE_PATH] == MIDDLEWARE_PRIORITY == 90
        assert middlewares[MIDDLEWARE_PATH] < middlewares[robots]
