"""#481: HiddenURLExtractor reachable from the scout parse path behind stage1.extract_hidden_urls."""

from prometheus_client import REGISTRY
from scrapy.http import HtmlResponse

from src.stage1.processors.hidden_url_extractor import HiddenURLExtractor
from src.stage1.processors.url_extractor import DISCOVERY_HEURISTICS, URLExtractor, resolve_heuristics

BASE = "https://uconn.edu/"
# Neither URL is visible to the default heuristics: meta refresh is not one of
# the meta tags URLExtractor reads, and loadData("/api/...") matches none of its
# JS patterns (no fetch/.get, no "//").
PAGE = """<html><head>
<meta http-equiv="refresh" content="0; url=/moved-here">
<script>loadData("/api/v2/news-feed");</script>
</head><body><a href="/normal">n</a></body></html>"""
REFRESH = "https://uconn.edu/moved-here"
API = "https://uconn.edu/api/v2/news-feed"


class FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


def _resp():
    return HtmlResponse(url=BASE, body=PAGE.encode(), encoding="utf-8")


def test_default_heuristics_miss_hidden_urls():
    found = URLExtractor(BASE, ["uconn.edu"], heuristics=list(DISCOVERY_HEURISTICS)).discover_all_urls(_resp())
    assert "https://uconn.edu/normal" in found
    assert REFRESH not in found and API not in found


def test_hidden_urls_is_off_by_default():
    assert "hidden_urls" not in resolve_heuristics(config=FakeConfig({}))
    assert "hidden_urls" not in resolve_heuristics({"raw_regex": False})


def test_config_flag_enables_hidden_extractor(monkeypatch):
    monkeypatch.setattr("src.core.config.get_config", lambda: FakeConfig({"stage1.extract_hidden_urls": True}))
    extractor = URLExtractor(BASE, ["uconn.edu"])
    assert "hidden_urls" in extractor.heuristics
    found = extractor.discover_all_urls(_resp())
    assert REFRESH in found
    assert API in found
    assert extractor.heuristic_counts["hidden_urls"] >= 2


def test_flag_false_keeps_it_off():
    assert "hidden_urls" not in resolve_heuristics(config=FakeConfig({"stage1.extract_hidden_urls": False}))
    assert "hidden_urls" not in resolve_heuristics(config=FakeConfig({"stage1.extract_hidden_urls": "false"}))


def test_mapping_can_enable_or_override_flag():
    assert "hidden_urls" in resolve_heuristics({"hidden_urls": True})
    assert "hidden_urls" in resolve_heuristics(["standard_tags", "hidden_urls"])
    cfg = FakeConfig({"stage1.extract_hidden_urls": True, "stage1.discovery_heuristics": {"hidden_urls": False}})
    assert "hidden_urls" not in resolve_heuristics(config=cfg)


def test_hidden_guessed_sitemaps_are_not_crawled():
    found = URLExtractor(BASE, ["uconn.edu"], heuristics=["hidden_urls"]).discover_all_urls(_resp())
    assert not any("sitemap" in u for u in found)


def test_hidden_urls_metric():
    def sample():
        return REGISTRY.get_sample_value("scrapy_url_extractor_urls_total", {"heuristic": "hidden_urls"}) or 0.0

    before = sample()
    extractor = URLExtractor(BASE, ["uconn.edu"], heuristics=["hidden_urls"])
    extractor.discover_all_urls(_resp())
    assert sample() - before == extractor.heuristic_counts["hidden_urls"] >= 2


def test_api_patterns_capture_full_path():
    """The capture groups used to grab only 'api'/'v2', collapsing every endpoint to /api."""
    resp = HtmlResponse(
        url=BASE,
        body=b'<script>x("/api/v2/news-feed"); y("/rest/items/list"); z("/graphql");</script>',
        encoding="utf-8",
    )
    hx = HiddenURLExtractor(BASE)
    api = set(hx.extract_api_endpoints(resp))
    assert {API, "https://uconn.edu/rest/items/list", "https://uconn.edu/graphql"} <= api
    assert "https://uconn.edu/api" not in api and "https://uconn.edu/rest" not in api
    js = set(hx.extract_from_javascript(resp))
    assert API in js and "https://uconn.edu/api" not in js


def test_config_yml_documents_flag_off():
    from src.core.config import get_config

    assert get_config().get("stage1.extract_hidden_urls") is False
