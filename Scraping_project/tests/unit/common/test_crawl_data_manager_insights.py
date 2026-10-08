"""src/common/crawl_data_manager.py: read/cache/refresh/invalidate paths, offline (#269)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.common.crawl_data_manager import CrawlDataManager

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def iso(days_ago: float, aware: bool = True) -> str:
    t = NOW - timedelta(days=days_ago)
    return t.isoformat() if aware else t.replace(tzinfo=None).isoformat()


class FakeDelta:
    """In-memory stand-in for DeltaHelper.read/count; records reads."""

    def __init__(self, tables=None):
        self.tables = {k: list(v) for k, v in (tables or {}).items()}
        self.reads: list[str] = []

    def read(self, table, columns=None):
        self.reads.append(table)
        if table not in self.tables:
            raise FileNotFoundError(table)
        return [{c: r.get(c) for c in columns} if columns else dict(r) for r in self.tables[table]]

    def count(self, table):
        if table not in self.tables:
            raise FileNotFoundError(table)
        return len(self.tables[table])


def disc(url, domain="uconn.edu", days_ago=1.0, js=0.0, depth=1, aware=True):
    return {"url": url, "domain": domain, "discovered_at": iso(days_ago, aware), "js_confidence": js,
            "depth": depth, "parent_url": None}


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(CrawlDataManager, "_now", staticmethod(lambda: state["now"]))
    return state


def mgr(tables, **kw):
    return CrawlDataManager(delta_manager=FakeDelta(tables), **kw)


def test_tz_aware_rows_are_analysed(clock):
    """utc_now_iso() writes '+00:00' timestamps; these used to raise TypeError and yield None."""
    m = mgr({"stage1_discovery": [disc("https://uconn.edu/a/x"), disc("https://uconn.edu/a/y", aware=False)]})
    ins = m.analyze_domain("uconn.edu")
    assert ins is not None and ins.total_urls_crawled == 2


@pytest.mark.parametrize("value,expected", [
    ("2026-10-08T12:00:00Z", NOW), ("2026-10-08T12:00:00+00:00", NOW), ("2026-10-08T08:00:00-04:00", NOW),
    ("2026-10-08T12:00:00", NOW), (NOW, NOW), ("", datetime(1970, 1, 1, tzinfo=UTC)),
    (None, datetime(1970, 1, 1, tzinfo=UTC)), ("garbage", datetime(1970, 1, 1, tzinfo=UTC)),
])
def test_parse_timestamp_always_returns_aware_utc(value, expected):
    assert CrawlDataManager(delta_manager=FakeDelta())._parse_timestamp(value) == expected


def test_lookback_window_excludes_old_rows_and_other_domains(clock):
    rows = [disc("https://uconn.edu/new"), disc("https://uconn.edu/old", days_ago=40),
            disc("https://yale.edu/x", domain="yale.edu")]
    ins = mgr({"stage1_discovery": rows}, lookback_days=30).analyze_domain("uconn.edu")
    assert ins.total_urls_crawled == 1


def test_insight_fields(clock):
    rows = [disc("https://uconn.edu/research/a/1", js=0.0, depth=1),
            disc("https://uconn.edu/research/a/2", js=0.9, depth=3),
            disc("https://uconn.edu/news/x", js=None, depth=None),
            disc("https://uconn.edu/", js=0.3, depth=2)]
    errors = [{"url": "https://uconn.edu/news/x", "domain": "uconn.edu", "error_type": "timeout"}]
    ins = mgr({"stage1_discovery": rows, "stage1_errors": errors}).analyze_domain("uconn.edu")
    assert ins.avg_js_confidence == pytest.approx((0.0 + 0.9 + 0.3) / 3)  # 0.0 counts; None does not
    assert ins.avg_depth == pytest.approx(2.0)
    assert ins.most_valuable_paths[0] == "/research/a"
    assert (ins.successful_crawls, ins.failed_crawls) == (3, 1)
    assert ins.success_rate == pytest.approx(0.75)
    assert ins.avg_content_quality == int(0.75 * 80 + (1 - 0.2) * 20)
    assert ins.recommended_spider == "scout"


def test_more_errors_than_discoveries_never_goes_negative(clock):
    errors = [{"url": f"https://uconn.edu/{i}", "domain": "uconn.edu"} for i in range(5)]
    ins = mgr({"stage1_discovery": [disc("https://uconn.edu/a")], "stage1_errors": errors}).analyze_domain("uconn.edu")
    assert ins.successful_crawls == 0 and ins.success_rate == 0.0 and ins.avg_content_quality >= 0


@pytest.mark.parametrize("js,depth,spider", [(0.7, 1, "js"), (0.1, 5, "depth"), (0.1, 1, "scout")])
def test_recommended_spider(clock, js, depth, spider):
    m = mgr({"stage1_discovery": [disc("https://uconn.edu/a", js=js, depth=depth)]})
    assert m.get_recommended_spider("uconn.edu") == spider


def test_missing_tables_and_unknown_domains_degrade_to_defaults(clock):
    m = mgr({})
    assert m.analyze_domain("uconn.edu") is None
    assert m.get_domain_value_boost("uconn.edu") == 0
    assert m.is_valuable_path("https://uconn.edu/a", "uconn.edu") is False
    assert m.get_recommended_spider("uconn.edu") is None
    assert m.get_avg_js_confidence("uconn.edu") == 0.0
    assert m.get_top_domains() == []
    assert m.get_statistics()["total_urls_discovered"] == 0


@pytest.mark.parametrize("n_err,depth,boost", [(0, 0, 30), (3, 0, 20), (6, 0, 10), (8, 0, 0), (10, 0, 0),
                                               (10, 10, -20)])
def test_domain_value_boost_bands(clock, n_err, depth, boost):
    # quality = success_rate * 80 + (1 - min(avg_depth / 10, 1)) * 20
    rows = [disc(f"https://uconn.edu/p{i}", depth=depth) for i in range(10)]
    errors = [{"url": "x", "domain": "uconn.edu"}] * n_err
    assert mgr({"stage1_discovery": rows, "stage1_errors": errors}).get_domain_value_boost("uconn.edu") == boost


def test_is_valuable_path_uses_top_two_segments(clock):
    m = mgr({"stage1_discovery": [disc("https://uconn.edu/Research/labs/x")]})
    assert m.is_valuable_path("https://uconn.edu/research/labs/y", "uconn.edu") is True
    assert m.is_valuable_path("https://uconn.edu/about", "uconn.edu") is False


# --- cache: read, refresh, invalidate --------------------------------------


def test_cache_hit_then_force_refresh_then_clear(clock):
    m = mgr({"stage1_discovery": [disc("https://uconn.edu/a")]})
    m.analyze_domain("uconn.edu")
    n = len(m.delta_manager.reads)
    m.analyze_domain("uconn.edu")
    assert len(m.delta_manager.reads) == n  # served from cache
    m.delta_manager.tables["stage1_discovery"].append(disc("https://uconn.edu/b"))
    assert m.analyze_domain("uconn.edu", force_refresh=True).total_urls_crawled == 2
    m.clear_cache()
    assert m._domain_cache == {} and m._cached_at == {} and m._cache_timestamp is None


def test_each_entry_expires_on_its_own_ttl(clock):
    """A fresh lookup for one domain must not extend the life of another's stale entry."""
    m = mgr({"stage1_discovery": [disc("https://uconn.edu/a"), disc("https://yale.edu/a", domain="yale.edu")]})
    m.analyze_domain("uconn.edu")
    clock["now"] = NOW + timedelta(hours=5)
    m.analyze_domain("yale.edu")
    clock["now"] = NOW + timedelta(hours=7)  # uconn entry is 7h old, yale 2h
    m.delta_manager.tables["stage1_discovery"].append(disc("https://uconn.edu/b"))
    assert m.analyze_domain("uconn.edu").total_urls_crawled == 2  # refreshed
    reads = len(m.delta_manager.reads)
    m.analyze_domain("yale.edu")
    assert len(m.delta_manager.reads) == reads  # still cached


def test_url_pattern_insights(clock):
    urls = [f"https://uconn.edu/news/{i}" for i in range(6)]
    stage2 = [{"url": u, "content_length": 4000} for u in urls[:3]] + [{"url": urls[0], "content_length": 4000}]
    m = mgr({"stage1_discovery": [{"url": u} for u in urls + ["https://uconn.edu/x"]],
             "stage2_page_analysis": stage2})
    ins = m.analyze_url_pattern(r"/news/")
    assert ins.match_count == 6
    assert ins.success_rate == pytest.approx(0.5)  # re-analysed page counted once
    assert ins.avg_content_length == 4000
    assert ins.recommended_priority == 40
    assert m.analyze_url_pattern(r"/nothing/") is None  # < 5 matches
    assert m.analyze_url_pattern(r"(") is None  # bad regex handled


def test_statistics(clock):
    m = mgr({"stage1_discovery": [disc("https://uconn.edu/a"), disc("https://yale.edu/a", domain="yale.edu")],
             "stage1_errors": [{"url": "x", "domain": "uconn.edu"}], "stage2_page_analysis": [{"url": "a"}]})
    s = m.get_statistics()
    assert (s["total_urls_discovered"], s["total_errors"], s["total_analyzed_pages"], s["unique_domains"]) == (2, 1, 1, 2)
    assert s["overall_success_rate"] == pytest.approx(2 / 3)
    assert s["cache_age_hours"] is None
