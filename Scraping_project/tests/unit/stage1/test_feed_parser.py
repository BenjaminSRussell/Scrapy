"""#643: RSS/Atom profile feeds enqueue item links onto seeds and stage2_queue."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import SeedManager
from src.stage1.discovery_dedup import DiscoveryDedup
from src.stage1.feed_parser import FeedFetchError, fetch_feed, parse_feed, profile_feed_urls
from src.stage1.scout_spider import ScoutSpider

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "feeds"
CONFIG = Path(__file__).resolve().parents[3] / "config.yml"
FEED_URL = "https://today.example.edu/rss.xml"
ATOM_URL = "https://today.example.edu/atom.xml"
RSS_LINKS = [
    "https://today.example.edu/2026/03/research-grant",
    "https://today.example.edu/2026/03/lecture-series",
    "https://today.example.edu/2026/03/campus-notes",
    "https://today.example.edu/2026/04/guid-only",
]
ATOM_LINKS = [
    "https://today.example.edu/2026/03/faculty-profile",
    "https://today.example.edu/2026/03/relative-story",
]


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Depth:
    def zcard(self, _key):
        return 0


def _spider(feed_urls, backend):
    spider = ScoutSpider.__new__(ScoutSpider)
    spider.feed_urls = list(feed_urls)
    spider.job_id = "job-643"
    spider.ack_empty_discovery = False
    spider.discovery_dedup = DiscoveryDedup(None, job_id="job-643")
    spider.scout_stats = {
        "urls_added_to_seeds": 0,
        "discovery_dedup_skipped": 0,
        "discovery_failures": 0,
    }
    spider.seed_manager = SeedManager(
        backend,
        redis_client=_Depth(),
        write_domain_urls=False,
        allowed_domains=["uconn.edu"],
    )
    spider._last_discovery_handoff = None
    return spider


@pytest.mark.unit
@pytest.mark.stage1
def test_shipped_profile_feeds_is_an_empty_list():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert cfg["stage1"]["feeds"] == []


@pytest.mark.unit
@pytest.mark.stage1
def test_profile_feed_urls_normalize():
    assert profile_feed_urls(_Config({})) == []
    assert profile_feed_urls(_Config({"stage1.feeds": []})) == []
    assert profile_feed_urls(_Config({"stage1.feeds": None})) == []
    assert profile_feed_urls(None) == []
    assert profile_feed_urls(_Config({"stages.stage1.feeds": [FEED_URL]})) == [FEED_URL]
    mixed = profile_feed_urls(
        _Config(
            {
                "stage1.feeds": [
                    FEED_URL,
                    f"  {FEED_URL}  ",
                    "not a url",
                    "ftp://files.example.edu/feed.xml",
                    "https://today.example.edu/atom.xml",
                    {"url": "https://today.example.edu/ignored"},
                ]
            }
        )
    )
    assert mixed == [FEED_URL, "https://today.example.edu/atom.xml"]
    # stage1.feeds wins over the legacy stages.stage1 prefix.
    assert profile_feed_urls(_Config({"stage1.feeds": [], "stages.stage1.feeds": [FEED_URL]})) == []


@pytest.mark.unit
@pytest.mark.stage1
def test_sample_rss_fixture_item_links_only():
    parsed = parse_feed((FIXTURES / "sample.rss").read_bytes(), feed_url=FEED_URL)
    assert parsed.kind == "rss"
    assert parsed.error is None
    assert parsed.urls == RSS_LINKS
    assert "https://today.example.edu/" not in parsed.urls
    assert "https://today.example.edu/should-not-enqueue" not in parsed.urls


@pytest.mark.unit
@pytest.mark.stage1
def test_sample_atom_fixture_skips_feed_and_enclosure_links():
    parsed = parse_feed((FIXTURES / "sample.atom").read_bytes(), feed_url=ATOM_URL)
    assert parsed.kind == "atom"
    assert parsed.urls == ATOM_LINKS
    assert "https://today.example.edu/" not in parsed.urls
    assert "https://today.example.edu/audio/faculty.mp3" not in parsed.urls
    assert "https://today.example.edu/atom.xml" not in parsed.urls


@pytest.mark.unit
@pytest.mark.stage1
def test_atom_id_fallback_and_rejects_unsafe_xml():
    body = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>https://today.example.edu/2026/05/id-only</id></entry>
    </feed>
    """
    parsed = parse_feed(body, feed_url=ATOM_URL)
    assert parsed.urls == ["https://today.example.edu/2026/05/id-only"]

    xxe = b"""<?xml version="1.0"?>
    <!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <rss version="2.0"><channel><item><link>&xxe;</link></item></channel></rss>
    """
    rejected = parse_feed(xxe, feed_url=FEED_URL)
    assert rejected.kind == "error"
    assert rejected.urls == []

    laughs = b"""<?xml version="1.0"?>
    <!DOCTYPE rss [<!ENTITY a "aa"><!ENTITY b "&a;&a;"><!ENTITY c "&b;&b;">]>
    <rss version="2.0"><channel><item><link>&c;</link></item></channel></rss>
    """
    bomb = parse_feed(laughs, feed_url=FEED_URL)
    assert bomb.kind == "error"
    assert bomb.urls == []

    html = parse_feed(b"<html><body>not a feed</body></html>", feed_url=FEED_URL)
    assert html.kind == "unknown"
    assert html.urls == []


@pytest.mark.unit
@pytest.mark.stage1
def test_fetch_refuses_link_local_without_a_request():
    with pytest.raises(FeedFetchError, match="refusing feed url"):
        fetch_feed("http://169.254.169.254/latest/meta-data")


@pytest.mark.unit
@pytest.mark.stage1
def test_rss_fixture_enqueues_seeds_and_stage2_queue(monkeypatch):
    backend = InMemoryBackend()
    spider = _spider([FEED_URL], backend)
    monkeypatch.setattr(
        "src.stage1.feed_parser.fetch_feed",
        lambda url, **kwargs: (FIXTURES / "sample.rss").read_bytes(),
    )
    spider._discover_and_add_feed_urls()

    seeds = backend.read("seed_urls")
    queue = backend.read("stage2_queue")
    assert [row["url"] for row in seeds] == RSS_LINKS
    assert [row["url"] for row in queue] == RSS_LINKS
    assert {row["status"] for row in queue} == {"pending"}
    assert {row["source_spider"] for row in seeds} == {"feed_parser"}
    assert spider.scout_stats["urls_added_to_seeds"] == len(RSS_LINKS)
    assert spider.scout_stats["discovery_failures"] == 0
    assert "uconn_urls" not in backend.tables


@pytest.mark.unit
@pytest.mark.stage1
def test_bad_feed_does_not_block_the_next_feed(monkeypatch):
    backend = InMemoryBackend()
    spider = _spider([FEED_URL, ATOM_URL], backend)
    bodies = {
        FEED_URL: b"<rss>",
        ATOM_URL: (FIXTURES / "sample.atom").read_bytes(),
    }
    monkeypatch.setattr("src.stage1.feed_parser.fetch_feed", lambda url, **kwargs: bodies[url])
    spider._discover_and_add_feed_urls()

    assert [row["url"] for row in backend.read("stage2_queue")] == ATOM_LINKS
    assert [row["url"] for row in backend.read("seed_urls")] == ATOM_LINKS
    assert spider.scout_stats["discovery_failures"] == 1
    assert spider.scout_stats["urls_added_to_seeds"] == len(ATOM_LINKS)
