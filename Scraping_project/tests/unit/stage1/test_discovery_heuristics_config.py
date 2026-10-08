"""#27: discovery heuristics can be switched off from config, without code changes."""

import logging

import pytest
from prometheus_client import REGISTRY
from scrapy.http import HtmlResponse

from src.stage1.processors.url_extractor import DISCOVERY_HEURISTICS, URLExtractor, resolve_heuristics

BASE = "https://uconn.edu/"

# One URL per heuristic. Absolute URLs are also visible to raw_regex, so the
# "disabled" checks below turn raw_regex off as well.
PAGE = """<html><head>
<meta property="og:url" content="/h-meta">
<script src="/h-script-tag.js"></script>
<script>var target = "/h-inline";</script>
<script type="application/ld+json">{"@type": "Thing", "sameAs": "/h-jsonld"}</script>
<style>.x { background: url('/h-css.png'); }</style>
</head><body>
<a href="/h-standard">std</a>
<div data-href="/h-data"></div>
<!-- old link https://uconn.edu/h-comment -->
<button onclick="go('https://uconn.edu/h-event')">go</button>
<p>Plain text mention https://uconn.edu/h-raw here</p>
</body></html>"""

EXPECTED = {
    "standard_tags": "https://uconn.edu/h-standard",
    "inline_scripts": "https://uconn.edu/h-inline",
    "script_tags": "https://uconn.edu/h-script-tag.js",
    "css": "https://uconn.edu/h-css.png",
    "data_attributes": "https://uconn.edu/h-data",
    "meta_tags": "https://uconn.edu/h-meta",
    "json_ld": "https://uconn.edu/h-jsonld",
    "comments": "https://uconn.edu/h-comment",
    "event_handlers": "https://uconn.edu/h-event",
    "raw_regex": "https://uconn.edu/h-raw",
}


class FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


def _response():
    return HtmlResponse(url=BASE, body=PAGE.encode(), encoding="utf-8")


def _discover(heuristics=None):
    return URLExtractor(BASE, ["uconn.edu"], heuristics=heuristics).discover_all_urls(_response())


def test_every_heuristic_has_a_fixture_and_an_extractor_method():
    assert set(EXPECTED) == set(DISCOVERY_HEURISTICS)
    for name in DISCOVERY_HEURISTICS:
        assert callable(getattr(URLExtractor, f"_extract_from_{name}"))


def test_default_runs_every_heuristic():
    found = _discover()
    for url in EXPECTED.values():
        assert url in found


@pytest.mark.parametrize("name", DISCOVERY_HEURISTICS)
def test_each_heuristic_alone_finds_its_url(name):
    assert EXPECTED[name] in _discover([name])


@pytest.mark.parametrize("name", DISCOVERY_HEURISTICS)
def test_each_heuristic_can_be_disabled(name):
    found = _discover({name: False, "raw_regex": False})
    assert EXPECTED[name] not in found
    others = [h for h in DISCOVERY_HEURISTICS if h not in (name, "raw_regex")]
    for other in others:
        assert EXPECTED[other] in found, other


def test_config_block_disables_heuristics(monkeypatch):
    cfg = FakeConfig({"stage1.discovery_heuristics": {"raw_regex": False, "comments": False}})
    monkeypatch.setattr("src.core.config.get_config", lambda: cfg)
    extractor = URLExtractor(BASE, ["uconn.edu"])
    assert "raw_regex" not in extractor.heuristics and "comments" not in extractor.heuristics
    found = extractor.discover_all_urls(_response())
    assert EXPECTED["raw_regex"] not in found
    assert EXPECTED["comments"] not in found
    assert EXPECTED["standard_tags"] in found


def test_legacy_stages_prefix_is_honoured():
    cfg = FakeConfig({"stages.stage1.discovery_heuristics": ["standard_tags"]})
    assert resolve_heuristics(config=cfg) == frozenset({"standard_tags"})


def test_string_false_values_disable():
    assert "css" not in resolve_heuristics({"css": "false"})
    assert "css" not in resolve_heuristics({"css": "off"})
    assert "css" in resolve_heuristics({"css": True})


def test_unknown_names_warn_and_are_ignored(caplog):
    with caplog.at_level(logging.WARNING):
        enabled = resolve_heuristics({"raw_regexp": False})
    assert enabled == frozenset(DISCOVERY_HEURISTICS)  # typo does not silently disable anything
    assert "raw_regexp" in caplog.text


def test_invalid_setting_falls_back_to_all(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_heuristics("raw_regex") == frozenset(DISCOVERY_HEURISTICS)
    assert "Ignoring discovery_heuristics" in caplog.text


def test_repo_config_lists_every_heuristic_enabled():
    from src.core.config import get_config

    block = get_config().get("stage1.discovery_heuristics")
    assert set(block) == set(DISCOVERY_HEURISTICS)
    assert all(v is True for v in block.values())


def test_per_heuristic_yield_is_counted():
    def sample(name):
        return REGISTRY.get_sample_value("scrapy_url_extractor_urls_total", {"heuristic": name}) or 0.0

    before = {h: sample(h) for h in DISCOVERY_HEURISTICS}
    extractor = URLExtractor(BASE, ["uconn.edu"])
    extractor.discover_all_urls(_response())
    assert extractor.heuristic_counts["standard_tags"] >= 1
    assert sample("standard_tags") - before["standard_tags"] == extractor.heuristic_counts["standard_tags"]
    assert sum(extractor.heuristic_counts.values()) == len(extractor.discovered_urls)


# --------------------------------------------------------------- scout flags
def _scout_with(monkeypatch, data):
    import src.core.config as core_config
    from src.stage1 import scout_spider

    monkeypatch.setattr(core_config, "get_config", lambda: FakeConfig(data))
    monkeypatch.setattr(scout_spider.ScoutSpider, "_discover_and_add_sitemap_urls", lambda self: None)
    monkeypatch.setattr(scout_spider, "SeedManager", lambda *a, **k: object())
    return scout_spider


def test_scout_reads_stage1_keys_from_config_yml_layout():
    """config.yml has ``stage1.parse_sitemaps``; scout used to read ``stages.stage1.*`` (always None)."""
    from src.stage1.scout_spider import _stage1_flag

    cfg = FakeConfig({"stage1.parse_sitemaps": False, "stage1.expand_seeds": False, "stage1.aggressive_collection": "no"})
    assert _stage1_flag(cfg, "parse_sitemaps", True) is False
    assert _stage1_flag(cfg, "expand_seeds", True) is False
    assert _stage1_flag(cfg, "aggressive_collection", True) is False
    assert _stage1_flag(FakeConfig({"stages.stage1.expand_seeds": False}), "expand_seeds", True) is False
    assert _stage1_flag(FakeConfig({}), "expand_seeds", True) is True


def test_scout_spider_honours_parse_sitemaps_false(monkeypatch):
    calls = []
    scout_spider = _scout_with(monkeypatch, {"stage1.parse_sitemaps": False, "stage1.expand_seeds": False})
    monkeypatch.setattr(scout_spider.ScoutSpider, "_discover_and_add_sitemap_urls", lambda self: calls.append(1))
    try:
        spider = scout_spider.ScoutSpider(start_urls=["https://uconn.edu/"])
    except Exception as exc:  # pragma: no cover - environment without delta/redis
        pytest.skip(f"ScoutSpider could not be constructed here: {exc}")
    assert spider.parse_sitemaps is False
    assert spider.expand_seeds is False
    assert calls == []
